FROM apache/airflow:3.0.6-python3.11

USER airflow

RUN pip install --no-cache-dir \
    "yfinance==0.2.*" \
    "pyiceberg[s3fs,pyarrow]==0.7.*" \
    "dbt-trino==1.8.*" \
    pandas \
    pyarrow \
    trino

RUN python -c "import yfinance, pyiceberg, pandas, pyarrow, trino; print('deps OK')"