FROM python:3.12-slim

COPY v1/ /srv/fixtures/v1/
COPY v2/ /srv/fixtures/v2/
COPY server.py /srv/server.py

RUN python -c "import shutil; shutil.copytree('/srv/fixtures/v1', '/srv/data/debian')"

EXPOSE 8000
WORKDIR /srv/data
CMD ["python", "/srv/server.py"]
