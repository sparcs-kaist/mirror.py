FROM python:3.12-slim

COPY v1/ /srv/fixtures/v1/
COPY v2/ /srv/fixtures/v2/

RUN python -c "import shutil; shutil.copytree('/srv/fixtures/v1', '/srv/data/debian')"

EXPOSE 8000
CMD ["python", "-m", "http.server", "8000", "--directory", "/srv/data"]
