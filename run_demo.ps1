$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$py = "C:\Users\35589\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
$root = "C:\Users\35589\Desktop\invsys"
$db = Join-Path $env:TEMP "inv_$(Get-Random).db"
$port = 8231
$base = "http://127.0.0.1:$port"

Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 300

function Post($path, $obj, $token) {
    $headers = @{ 'Content-Type' = 'application/json; charset=utf-8' }
    if ($token) { $headers['Authorization'] = "Bearer $token" }
    $body = [System.Text.Encoding]::UTF8.GetBytes(($obj | ConvertTo-Json -Compress -Depth 5))
    $r = Invoke-WebRequest -Uri "$base$path" -Method Post -Headers $headers -Body $body -UseBasicParsing
    return @{ Status = [int]$r.StatusCode; Body = ($r.Content | ConvertFrom-Json) }
}
function Get2($path, $token) {
    $h = @{}
    if ($token) { $h['Authorization'] = "Bearer $token" }
    $r = Invoke-WebRequest -Uri "$base$path" -Headers $h -UseBasicParsing
    return @{ Status = [int]$r.StatusCode; Body = ($r.Content | ConvertFrom-Json) }
}

Write-Output "########## 启动 ##########"
$proc = Start-Process -FilePath $py -ArgumentList '-m','invsys','--port',$port,'--db',$db -WorkingDirectory $root -PassThru -RedirectStandardOutput "$env:TEMP\inv_out.log" -RedirectStandardError "$env:TEMP\inv_err.log"
Start-Sleep -Seconds 2
Get-Content "$env:TEMP\inv_out.log" -Encoding UTF8 | Select-Object -First 3

$login = Post "/api/login" @{ username='admin'; password='admin123' }
$token = $login.Body.token
Write-Output ""
Write-Output ("登录 -> " + $login.Status + "  role=" + $login.Body.role)

$p = Post "/api/products" @{ sku='SKU-001'; name='矿泉水'; unit='箱'; safe_stock=10; category='饮料' } $token
$w = Post "/api/warehouses" @{ code='WH-01'; name='主仓' } $token
Write-Output ("建商品 -> " + $p.Status + " id=" + $p.Body.id + " " + $p.Body.name)
Write-Output ("建仓库 -> " + $w.Status + " id=" + $w.Body.id + " " + $w.Body.name)

Write-Output ""
$buy = Post "/api/purchase" @{ no='P-001'; warehouse_id=$w.Body.id; items=@(@{ sku='SKU-001'; qty=100; price=5 }) } $token
Write-Output ("采购入库 100 箱 -> " + $buy.Status + "  金额=" + $buy.Body.total_amount)

$sale = Post "/api/sale" @{ no='S-001'; warehouse_id=$w.Body.id; items=@(@{ sku='SKU-001'; qty=30; price=9 }) } $token
Write-Output ("销售出库 30 箱 -> " + $sale.Status + "  金额=" + $sale.Body.total_amount)

$stock = Get2 "/api/stock?warehouse_id=$($w.Body.id)" $token
Write-Output ("当前库存 -> " + $stock.Body[0].qty + " 箱（低于安全库存: " + $stock.Body[0].below_safe + "）")

Write-Output ""
Write-Output "########## 库存不足：申请卖 500 箱 ##########"
try {
    Post "/api/sale" @{ no='S-002'; warehouse_id=$w.Body.id; items=@(@{ sku='SKU-001'; qty=500 }) } $token | Out-Null
} catch {
    $s = $_.Exception.Response.GetResponseStream()
    $reader = New-Object System.IO.StreamReader($s, [System.Text.Encoding]::UTF8)
    Write-Output ("结果 -> " + [int]$_.Exception.Response.StatusCode + "  " + $reader.ReadToEnd())
}
$after = Get2 "/api/stock?warehouse_id=$($w.Body.id)" $token
$doc404 = $null
try { Get2 "/api/docs/S-002" $token | Out-Null } catch { $doc404 = [int]$_.Exception.Response.StatusCode }
Write-Output ("库存没变 -> " + $after.Body[0].qty + " 箱；S-002 单据是否存在 -> " + $doc404 + "（404 表示整单回滚）")

Write-Output ""
Write-Output "########## 库存流水（可对账） ##########"
$mv = Get2 "/api/movements" $token
foreach ($m in $mv.Body) {
    Write-Output ("  " + $m.type.PadRight(9) + " " + $m.qty_delta.ToString().PadLeft(5) + "  " + $m.qty_before + " -> " + $m.qty_after)
}

Write-Output ""
Write-Output "########## 盘点：实盘 68 箱 ##########"
$adj = Post "/api/adjust" @{ no='A-001'; warehouse_id=$w.Body.id; items=@(@{ sku='SKU-001'; qty=68 }) } $token
$mv2 = Get2 "/api/movements?limit=1" $token
Write-Output ("盘点单 -> " + $adj.Status + "  差异流水: " + $mv2.Body[0].qty_before + " -> " + $mv2.Body[0].qty_after + " (delta " + $mv2.Body[0].qty_delta + ")")

Write-Output ""
Write-Output "########## 报表 ##########"
$sum = Get2 "/api/report/summary" $token
Write-Output ("汇总 -> 商品=" + $sum.Body.products + " 仓库=" + $sum.Body.warehouses + " 库存总量=" + $sum.Body.stock_total + " 单据=" + $sum.Body.docs + " 销售额=" + $sum.Body.sales_amount)
$low = Get2 "/api/report/low-stock" $token
Write-Output ("低库存预警 -> " + $low.Body.Count + " 条")

Write-Output ""
$health = Get2 "/api/health"
Write-Output ("健康检查 -> " + $health.Body.status + "  schema=" + $health.Body.schema)

$proc.Kill()
Remove-Item $db -Force -ErrorAction SilentlyContinue
Write-Output ""
Write-Output "已停止服务。"
