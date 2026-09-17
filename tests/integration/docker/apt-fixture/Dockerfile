FROM python:3.12-slim

RUN python -m pip install --no-cache-dir aioftp==0.23.1

COPY v1/ /srv/fixtures/v1/
COPY v2/ /srv/fixtures/v2/
COPY server.py /srv/server.py

RUN python -c "import shutil; shutil.copytree('/srv/fixtures/v1', '/srv/data')"

EXPOSE 8000 2121
CMD ["python", "/srv/server.py"]
