"""Monitoring the scaffold emits — the part that can say "wrong", not only
"absent".

Liveness rules answer "is it up". The failure this layer exists for is a
container that IS up, IS healthy, answers 200, and serves 500s because its
schema is behind its code. So the emitted rule set is four facts, not one:

  Container Down            reachability      (kept: an OOM kill does not
                                               crash-loop, and reachability
                                               is the only thing that sees it)
  Service Down              scrape liveness
  Container Restarting      crash loop, keyed on a MONOTONIC restart counter
                            rather than reachability - a reachability rule on
                            a container that restarts every minute crosses its
                            threshold in both directions and sends a fresh
                            notification per crossing, hundreds of messages
                            about one unchanged fact
  Schema Behind Code        correctness
  Schema Probe Cannot Answer  the OTHER fact - and it must not borrow the
                            sentence above it

noDataState
-----------
The default here is ``NoData``. "The metric source died" is not evidence of
health: with ``OK`` a board goes green the moment the exporter is killed, and
with ``Alerting`` the rule fires its own summary text for something it never
measured. Grafana's ``NoData`` raises a separate ``DatasourceNoData`` alert
with its own identity, which is the same discipline the two schema rules
follow - one fact, one sentence. A stand that is deliberately switched off is
then a ROUTING decision (a mute timing, a notification policy), not a reason
to teach every generated project that blindness looks like health.

One argued exception: ``Schema Behind Code`` keeps ``noDataState: OK``,
because its series is deliberately absent when the schema state could not be
determined - see the probe. There, absence is designed, and the gap it leaves
is closed by ``Schema Probe Cannot Answer``, whose series IS emitted
unconditionally.
"""

PROMETHEUS_YML = """\
# Prometheus scrape config. `stapel-new-service` appends a job per service.
global:
  scrape_interval: 30s
  evaluation_interval: 30s

scrape_configs:
  - job_name: 'prometheus'
    static_configs:
      - targets: ['localhost:9090']

  # Container-level metrics (restart counters, last-seen) behind the
  # Container Down / Container Restarting rules.
  - job_name: 'cadvisor'
    static_configs:
      - targets: ['cadvisor:8080']

  # Redis and PostgreSQL exporters: add jobs here when you deploy them.
"""

GRAFANA_DATASOURCE_YAML = """\
apiVersion: 1

datasources:
  # uid is pinned: the alert rules reference `datasourceUid: prometheus`.
  - uid: prometheus
    name: Prometheus
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
    editable: false
"""

# Container selection is by compose LABEL, not by a list of names. A selector
# that does not name a container is silent about it forever, which reads
# exactly like healthy - and a hand-maintained name list acquires that gap the
# first time someone adds a service.
_CONTAINER_SELECTOR = (
    'container_label_com_docker_compose_project!="", '
    'container_label_com_docker_compose_service!~"prometheus|grafana|cadvisor|frontend-build"'
)

GRAFANA_ALERT_RULES_YAML = """\
# Grafana unified-alerting rules, provisioned as files.
#
# Wire a contact point and a notification policy for this stack before
# trusting it (Grafana UI, or another file in this directory) - a rule that
# fires into nothing is a rule that did not fire.
#
# noDataState is `NoData` on every rule but one, deliberately. "The metric
# source died" is not evidence of health: with `OK` the board turns green the
# moment the exporter is killed, and with `Alerting` the rule announces its own
# summary for something it never measured. `NoData` raises Grafana's separate
# DatasourceNoData alert, which carries its own identity - blindness and
# breakage stay two facts with two sentences. A stand that is deliberately
# switched off is a routing decision (mute timing / notification policy), not a
# reason to make blindness look like health.
apiVersion: 1

groups:
  - orgId: 1
    name: Container Health
    folder: Alerts
    interval: 1m
    rules:

      - uid: stapel-container-down
        title: Container Down
        condition: threshold
        data:
          - refId: last_seen_age
            queryType: ''
            relativeTimeRange:
              from: 300
              to: 0
            datasourceUid: prometheus
            model:
              # Selected by compose label rather than by name - see the note
              # in the generator. The monitoring containers are excluded: a
              # dead Prometheus cannot alert on its own absence, and pretending
              # otherwise here would be worse than the honest gap.
              # frontend-build is excluded because it is a one-shot writer that
              # is SUPPOSED to be gone.
              expr: >
                (time() - container_last_seen{%(selector)s})
              refId: last_seen_age
              instant: true
          - refId: threshold
            queryType: ''
            relativeTimeRange:
              from: 300
              to: 0
            datasourceUid: __expr__
            model:
              type: threshold
              refId: threshold
              expression: last_seen_age
              conditions:
                - type: query
                  evaluator:
                    type: gt
                    params: [120]
                  operator:
                    type: and
                  reducer:
                    type: last
                  query:
                    params: [threshold]
        noDataState: NoData
        execErrState: Error
        for: 2m
        annotations:
          summary: "Container <code>{{ $labels.name }}</code> has been unreachable for 2+ minutes"
        labels:
          severity: critical
        isPaused: false

      - uid: stapel-service-down
        title: Service Down
        condition: threshold
        data:
          - refId: service_up
            queryType: ''
            relativeTimeRange:
              from: 120
              to: 0
            datasourceUid: prometheus
            model:
              expr: up{job!="prometheus"}
              refId: service_up
              instant: true
          - refId: threshold
            queryType: ''
            relativeTimeRange:
              from: 120
              to: 0
            datasourceUid: __expr__
            model:
              type: threshold
              refId: threshold
              expression: service_up
              conditions:
                - type: query
                  evaluator:
                    type: lt
                    params: [1]
                  operator:
                    type: and
                  reducer:
                    type: last
                  query:
                    params: [threshold]
        noDataState: NoData
        execErrState: Error
        for: 2m
        annotations:
          summary: "Service <code>{{ $labels.job }}</code> is not responding to Prometheus scrapes"
        labels:
          severity: critical
        isPaused: false

      # A crash loop is a different fact from "unreachable", and keying it on
      # reachability is what turns one unchanged situation into hundreds of
      # notifications: a container that comes back every minute crosses the
      # threshold in both directions, and every crossing is a fresh firing
      # after a resolve (repeat_interval only suppresses repeats of an ONGOING
      # alert, so no grouping setting can help).
      #
      # container_start_time_seconds steps up on each restart of the same
      # container - compose restarts in place, so the series is stable - and
      # changes() over 15m only falls back to zero once the loop has actually
      # stopped. Monotone while the fault lasts: one message, not two hundred.
      #
      # ADDITIVE. Container Down stays: a container killed by the OOM reaper
      # does not restart-loop, and reachability is the only thing that sees it.
      - uid: stapel-container-restarting
        title: Container Restarting
        condition: threshold
        data:
          - refId: restarts
            queryType: ''
            relativeTimeRange:
              from: 900
              to: 0
            datasourceUid: prometheus
            model:
              expr: >
                changes(container_start_time_seconds{%(selector)s}[15m])
              refId: restarts
              instant: true
          - refId: threshold
            queryType: ''
            relativeTimeRange:
              from: 900
              to: 0
            datasourceUid: __expr__
            model:
              type: threshold
              refId: threshold
              expression: restarts
              conditions:
                - type: query
                  evaluator:
                    type: gt
                    params: [3]
                  operator:
                    type: and
                  reducer:
                    type: last
                  query:
                    params: [threshold]
        noDataState: NoData
        execErrState: Error
        for: 5m
        annotations:
          summary: "Container <code>{{ $labels.name }}</code> is restarting repeatedly (crash loop, 4+ restarts in 15 minutes)"
        labels:
          severity: critical
        isPaused: false

  # Liveness is not correctness. Every rule above asks whether something is up.
  # The container that causes the expensive kind of outage is up, healthy,
  # answering 200, and serving 500s because its schema is missing a migration
  # its code requires. No liveness rule can catch that, because none of them
  # ask.
  - orgId: 1
    name: Service Correctness
    folder: Alerts
    interval: 1m
    rules:

      - uid: stapel-schema-behind
        title: Schema Behind Code
        condition: threshold
        data:
          - refId: schema_ok
            queryType: ''
            relativeTimeRange:
              from: 300
              to: 0
            datasourceUid: prometheus
            model:
              # Emitted by config/schema_health.py ONLY when the schema state
              # was actually determined. An unreachable database makes the
              # series STOP rather than drop to zero, so a database restart
              # cannot make this rule say "unapplied migration". Undetermined
              # has its own rule below, with its own sentence.
              expr: stapel_schema_at_head{}
              refId: schema_ok
              instant: true
          - refId: threshold
            queryType: ''
            relativeTimeRange:
              from: 300
              to: 0
            datasourceUid: __expr__
            model:
              type: threshold
              refId: threshold
              expression: schema_ok
              conditions:
                - type: query
                  evaluator:
                    type: lt
                    params: [1]
                  operator:
                    type: and
                  reducer:
                    type: last
                  query:
                    params: [threshold]
        # The one rule that keeps OK, and the only one that has earned it:
        # absence of THIS series means "the state could not be determined",
        # which is a real and blameless condition, deliberately expressed by
        # not emitting the metric. The gap that leaves - a rule that can never
        # fire because its subject never appears - is closed by Schema Probe
        # Cannot Answer below, whose series IS emitted unconditionally.
        noDataState: OK
        execErrState: Error
        # Longer than the container rules: a rolling migration legitimately
        # leaves a process behind head for a short window. Ten minutes is not
        # a rolling migration, it is a failed one.
        for: 10m
        annotations:
          summary: "Service <code>{{ $labels.service }}</code> is serving on a schema behind its code - unapplied migration"
        labels:
          severity: critical
        isPaused: false

      # A different fact, and it must not borrow the sentence above. "I cannot
      # tell you whether the schema is at head" is not "the schema is behind";
      # conflating the two is exactly why the probe has three states. Long fuse
      # on purpose: a database restart blinds every probe in the fleet for a
      # minute or two and that is not worth a page. Thirty minutes of not being
      # able to answer is a stuck probe, a broken migration graph, or a
      # database that never came back.
      - uid: stapel-schema-probe-blind
        title: Schema Probe Cannot Answer
        condition: threshold
        data:
          - refId: probe_ok
            queryType: ''
            relativeTimeRange:
              from: 1800
              to: 0
            datasourceUid: prometheus
            model:
              # Emitted on every scrape whatever the outcome, so unlike
              # schema_at_head this series going missing means the exporter
              # itself is gone.
              expr: stapel_schema_probe_ok{}
              refId: probe_ok
              instant: true
          - refId: threshold
            queryType: ''
            relativeTimeRange:
              from: 1800
              to: 0
            datasourceUid: __expr__
            model:
              type: threshold
              refId: threshold
              expression: probe_ok
              conditions:
                - type: query
                  evaluator:
                    type: lt
                    params: [1]
                  operator:
                    type: and
                  reducer:
                    type: last
                  query:
                    params: [threshold]
        noDataState: NoData
        execErrState: Error
        for: 30m
        annotations:
          summary: "Service <code>{{ $labels.service }}</code> has been unable to determine its schema state for 30 minutes - drift would not be detected"
        labels:
          severity: warning
        isPaused: false
""" % {"selector": _CONTAINER_SELECTOR}

MONITORING_COMPOSE_YML = """\
# Opt-in monitoring overlay. The alert rules in
# service-configs/grafana/provisioning/alerting/ are read by the Grafana here;
# without this file they are provisioning nobody loads.
#
#   docker compose --env-file .env -f docker-compose.yml \\
#                  -f docker-compose.monitoring.yml up -d
#
# Set GRAFANA_ADMIN_PASSWORD in the env file. Nothing is published except
# Grafana's own port - Prometheus and cAdvisor stay on the compose network.
services:

  prometheus:
    image: prom/prometheus:v3.1.0
    restart: unless-stopped
    volumes:
      - ./service-configs/prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - prometheus-data:/prometheus
    command:
      - '--config.file=/etc/prometheus/prometheus.yml'
      - '--storage.tsdb.retention.time=15d'

  # Container-level metrics. The restart counter behind Container Restarting
  # comes from here, so losing cAdvisor loses BOTH container rules - which is
  # why their noDataState is NoData and not OK.
  cadvisor:
    image: gcr.io/cadvisor/cadvisor:v0.52.1
    restart: unless-stopped
    privileged: true
    devices:
      - /dev/kmsg
    volumes:
      - /:/rootfs:ro
      - /var/run:/var/run:ro
      - /sys:/sys:ro
      - /var/lib/docker/:/var/lib/docker:ro

  grafana:
    image: grafana/grafana:11.6.1
    restart: unless-stopped
    depends_on:
      - prometheus
    ports:
      - "${GRAFANA_PORT:-3001}:3000"
    environment:
      GF_SECURITY_ADMIN_PASSWORD: "${GRAFANA_ADMIN_PASSWORD:?set GRAFANA_ADMIN_PASSWORD in the env file}"
      GF_USERS_ALLOW_SIGN_UP: "false"
    volumes:
      - ./service-configs/grafana/provisioning:/etc/grafana/provisioning:ro
      - grafana-data:/var/lib/grafana

volumes:
  prometheus-data:
  grafana-data:
"""
