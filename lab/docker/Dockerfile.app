# Minimal image for the simulated business app (stdlib only, no pip installs).
FROM python:3.12-slim

WORKDIR /opt/lab
# The whole lab/ tree is copied so sim/*.py sibling imports work as-is.
COPY . /opt/lab

EXPOSE 8080
ENV APP_PORT=8080
ENV DRILL_SCENARIO=""

# Optionally preload a fault scenario for manual demos via DRILL_SCENARIO.
CMD ["/bin/sh", "-c", "python3 sim/app.py --host 0.0.0.0 --port ${APP_PORT} ${DRILL_SCENARIO:+--scenario $DRILL_SCENARIO}"]
