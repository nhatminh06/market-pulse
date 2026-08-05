-- Postgres only creates the database named by POSTGRES_DB (airflow) on first
-- init. Superset connects to a separate "superset" database on the same
-- instance (see docker-compose.yaml SQLALCHEMY_DATABASE_URI), so it must be
-- created explicitly. Mounted into /docker-entrypoint-initdb.d/, which the
-- official postgres image only runs once, on an empty data directory.
CREATE DATABASE superset;
