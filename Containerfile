# CapableDeputy v0.1 container image.
#
# Architecture: stateless container. Persistent state (state.db,
# audit.jsonl, optional user data) lives on a bind-mounted volume at
# /var/lib/capabledeputy. The Unix socket lives at /run/capdep/capdep.sock,
# also bind-mounted out so terminal clients on the host can talk to the
# daemon without going over the network.
#
# Build:
#   podman build -t capabledeputy:0.1 .
#
# Run (foreground, tmux-style):
#   mkdir -p ~/.local/share/capabledeputy ~/.run/capdep
#   podman run --rm -it \
#     --name capdep \
#     -e ANTHROPIC_API_KEY \
#     -v ~/.local/share/capabledeputy:/var/lib/capabledeputy \
#     -v ~/.run/capdep:/run/capdep \
#     -e CAPDEP_DATA_DIR=/var/lib/capabledeputy \
#     -e CAPDEP_SOCKET=/run/capdep/capdep.sock \
#     capabledeputy:0.1
#
# Then on the host (different terminal):
#   CAPDEP_SOCKET=$HOME/.run/capdep/capdep.sock \
#     uv run capdep session list

FROM python:3.14-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN pip install --no-cache-dir uv==0.9.27

WORKDIR /build

COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src

RUN uv sync --frozen --no-dev --compile-bytecode

FROM python:3.14-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CAPDEP_DATA_DIR=/var/lib/capabledeputy \
    CAPDEP_SOCKET=/run/capdep/capdep.sock \
    CAPDEP_LLM_MODEL=claude-haiku-4-5

RUN useradd --system --create-home --uid 1500 capdep \
    && mkdir -p /var/lib/capabledeputy /run/capdep \
    && chown -R capdep:capdep /var/lib/capabledeputy /run/capdep

COPY --from=builder --chown=capdep:capdep /build /opt/capabledeputy

USER capdep
WORKDIR /home/capdep

VOLUME ["/var/lib/capabledeputy", "/run/capdep"]

ENV PATH="/opt/capabledeputy/.venv/bin:${PATH}"

ENTRYPOINT ["capdep"]
CMD ["daemon", "start"]
