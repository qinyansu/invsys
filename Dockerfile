FROM python:3.12-slim

WORKDIR /app
COPY invsys ./invsys
COPY tests ./tests
COPY README.md CHANGELOG.md ./

ENV PYTHONUNBUFFERED=1
VOLUME ["/data"]
EXPOSE 8000

CMD ["python", "-m", "invsys", "--port", "8000", "--db", "/data/invsys.db"]
