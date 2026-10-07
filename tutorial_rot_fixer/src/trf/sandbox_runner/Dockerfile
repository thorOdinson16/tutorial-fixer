# Build (smoke):  docker build -t trf-sandbox:latest src/trf/sandbox_runner
# Env image:      docker build --build-arg PYTHON_VERSION=3.12 --build-arg REQUIREMENTS="pandas==2.2.3" ...
# Dependencies are installed at BUILD time (network on). Every RUN uses --network none
# --read-only --cap-drop ALL + mem/cpu/pids limits (see nodes/sandbox.py).
ARG PYTHON_VERSION=3.12
FROM python:${PYTHON_VERSION}-slim

ARG REQUIREMENTS=""
ARG OPTIONAL_REQUIREMENTS=""

# REQUIREMENTS must install exactly (build fails otherwise). OPTIONAL ones are best-effort:
# a wrong guess (e.g. a tutorial-local module name) then surfaces as ImportError at run time.
RUN if [ -n "$REQUIREMENTS" ]; then pip install --no-cache-dir $REQUIREMENTS; fi \
 && for r in $OPTIONAL_REQUIREMENTS; do pip install --no-cache-dir "$r" || echo "TRF_SKIPPED $r"; done

RUN useradd --create-home --uid 10001 sandbox
COPY runner.py /opt/runner/runner.py

USER sandbox
WORKDIR /home/sandbox
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MPLBACKEND=Agg MPLCONFIGDIR=/tmp/mpl
ENTRYPOINT ["python", "/opt/runner/runner.py"]
