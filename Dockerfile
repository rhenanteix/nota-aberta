# nota-aberta no cluster k3s (sistema de apps/sistemas, imagem publicada pelo cd-imagens-k3s.yml).
#
# 1º estágio: compila o motor OFICIAL da Receita Federal (MIT) num commit fixo, sem tocar no código
# dele, e gera o SQLite das tabelas pelo flyway deles. Atualizar o motor = trocar RTC_COMMIT (e a
# versão em README.md) num PR; o teste de paridade (tests/, RTC_MOTOR_REAL) roda contra o jar novo.
# 2º estágio: JRE + python3 para a porta (servidor.py; o python3-minimal não traz json nem http.server). Raiz só leitura: o banco é aberto em modo
# ro pelo próprio perfil `offline` do motor; o Java escreve só em /tmp.
#
#   docker build -t nota-aberta apps/sistemas/nota-aberta
#   docker run --rm -p 8080:8080 --read-only --tmpfs /tmp nota-aberta
FROM maven:3.9-eclipse-temurin-21@sha256:99e61abcff91a9b1333463bd8451fb18495d6eba9250ac66a338b518f8278320 AS motor
ARG RTC_REPO=https://github.com/ReceitaFederal/calculadora-rtc-regime-regular.git
# Commit registrado na Portaria Cotec nº 342/2026 (DOU de 13/11/2026, art. 2º, § 1º, I, b) como a versão
# inicial publicada (1.6.1). Trocar por outro exige rodar a paridade; tests/ confere o valor.
ARG RTC_COMMIT=150cccbe414d4a5edea703e4f7a424af5aa222c7
WORKDIR /src
RUN git init -q . && git remote add origin "$RTC_REPO" \
 && git fetch -q --depth 1 origin "$RTC_COMMIT" && git checkout -q FETCH_HEAD \
 && test "$(git rev-parse HEAD)" = "$RTC_COMMIT"
RUN mkdir -p calculadora/db \
 && mvn -q -B flyway:migrate -Dflyway.configFiles=./flyway/flyway-pro.conf \
 && mvn -q -B -DskipTests package \
 && cp target/api-regime-geral.jar /src/motor.jar
# O driver SQLite extrai a biblioteca nativa em /tmp na partida, e /tmp montado `noexec` (o
# `--tmpfs` do Docker é assim) dá UnsatisfiedLinkError e o motor não sobe. Extraída aqui, ela fica
# na raiz só leitura e o driver a carrega por org.sqlite.lib.path (RTC_JAVA_OPTS).
RUN mkdir -p /tmp/x /src/native && cd /tmp/x \
 && LIB=$(jar tf /src/motor.jar | grep -m1 'BOOT-INF/lib/sqlite-jdbc-.*\.jar$') \
 && jar xf /src/motor.jar "$LIB" && jar xf "$LIB" "org/sqlite/native/Linux/$(uname -m)/libsqlitejdbc.so" \
 && cp "org/sqlite/native/Linux/$(uname -m)/libsqlitejdbc.so" /src/native/ && rm -rf /tmp/x

# 2º motor: DANFSe Service da Receita (Go, MIT), commit registrado na Portaria Cotec 342/2026 (art. 2º, § 1º, III, b).
# Gera o PDF oficial da NFS-e a partir do XML. Compila estático (CGO_ENABLED=0) e roda ao lado, em 127.0.0.1:8093.
FROM golang:1.27.1@sha256:162be5298a40ed317005c8339c6de4d10d3eef336d66dc8e9259b03ab9d3a6d2 AS danfse
ARG DANFSE_REPO=https://github.com/ReceitaFederal/calculadora-rtc-gerador-danfse.git
ARG DANFSE_COMMIT=8d79e6b2f7fad6375340afcb51c85ed370c64a14
WORKDIR /src
RUN git init -q . && git remote add origin "$DANFSE_REPO" \
 && git fetch -q --depth 1 origin "$DANFSE_COMMIT" && git checkout -q FETCH_HEAD \
 && test "$(git rev-parse HEAD)" = "$DANFSE_COMMIT" \
 && CGO_ENABLED=0 go build -o /src/danfse-service ./cmd/danfse

FROM eclipse-temurin:21-jre-noble@sha256:0d57a8e90c2bb844c5a8e1b2b71e11eff06dad7edcfd803cd21ed4ae203b2515
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv tzdata \
 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /tmp/requirements.txt
RUN python3 -m venv /venv && /venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt && rm /tmp/requirements.txt
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080 \
    RTC_JAR=/rtc/motor.jar RTC_DIR=/rtc RTC_DANFSE=/rtc/danfse-service TZ=America/Sao_Paulo \
    RTC_JAVA_OPTS="-XX:+UseSerialGC -Xmx256m -Xss512k -XX:MaxMetaspaceSize=256m -XX:ReservedCodeCacheSize=64m -XX:TieredStopAtLevel=1 -Djava.io.tmpdir=/tmp -Dorg.sqlite.lib.path=/rtc/native -Dorg.sqlite.lib.name=libsqlitejdbc.so"
COPY --from=motor /src/motor.jar /rtc/motor.jar
COPY --from=motor /src/calculadora/db/calculadora-pro.db /rtc/calculadora/db/calculadora-pro.db
COPY --from=motor /src/native/libsqlitejdbc.so /rtc/native/libsqlitejdbc.so
COPY --from=motor /src/LICENSE /rtc/LICENSE-receita-federal
COPY --from=danfse /src/danfse-service /rtc/danfse-service
WORKDIR /app
RUN mkdir -p /data && chown 10001:10001 /data
COPY *.py planos.json sistema.json ./
COPY web ./web
USER 10001
EXPOSE 8080
CMD ["/venv/bin/python", "servidor.py"]
