FROM apache/airflow:3.0.6-python3.11

USER root

RUN pip install --no-cache-dir \
    "yfinance==0.2.*" \
    "pyiceberg[s3fs,pyarrow]==0.7.*" \
    "dbt-trino==1.8.*" \
    pandas \
    pyarrow \
    trino

# Fail the build immediately if any package didn't land where the airflow
# user's runtime Python will actually look for it.
RUN python -c "import yfinance, pyiceberg, pandas, pyarrow, trino; print('deps OK')"

USER airflow