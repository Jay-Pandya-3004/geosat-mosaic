#!/usr/bin/env bash
# Tomcat's own env-setup hook -- sourced automatically by bin/catalina.sh
# before every start/stop if present at bin/setenv.sh. This is the
# officially-documented way to set CATALINA_OPTS, and unlike a systemd
# unit's `Environment=` line, a shell script has no word-splitting
# footgun: the RHEL box's `Environment=CATALINA_OPTS=-Xms1g -Xmx4g` (one
# unquoted line) silently became just `CATALINA_OPTS=-Xms1g` at runtime
# (per `ps aux | grep java` on the actual running
# process -- no -Xmx flag reached the JVM at all), because systemd splits
# unquoted Environment= values on whitespace into separate assignments.
# This file can't have that problem -- it's a bash script, the
# whole string is one argument to one variable assignment.
#
# Heap sized for the target Windows laptop's 32GB RAM (vs. the RHEL box's
# tight 15GB, where -Xmx4g -- even if it HAD taken effect -- would already
# have been a meaningful fraction of total memory). 8g leaves comfortable
# room for xcube, FastAPI, WSL2's own overhead, and the Windows host
# itself under WSL2's shared memory budget (.wslconfig's own `memory=`
# setting needs to be large enough to cover
# this alongside the other two services). Re-tune based on observed
# usage once this is actually running under load -- this is a
# reasoned starting point, not a measured-optimal number.
export CATALINA_OPTS="-Xms1g -Xmx8g"
