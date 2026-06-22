FROM apache/airflow:3.0.6-python3.11

USER root

# Install uv
RUN curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh

# Install pipeline dependencies into the system Python as root
# (same Python the airflow user runs — just need root to write to site-packages)
RUN uv pip install --system --no-cache \
    "yfinance==0.2.*" \
    "pyiceberg[s3fs,pyarrow]==0.7.*" \
    "dbt-trino==1.8.*" \
    pandas \
    pyarrow \
    trino

# Switch back to airflow for runtime
USER airflow