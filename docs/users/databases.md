# Databases on InterGenOS

Every major FOSS database category is available to install via pkm install; nothing is pre-installed in the desktop image by default, allowing you to choose exactly what your workload needs. 

InterGenOS offers 14 curated databases across relational SQL, NoSQL document, Key-Value (KV) cache, embedded KV, time-series, distributed KV, and wide-column categories. 

## 1. Quick Chooser

Not sure which one to pick? Match your primary use case to our recommended packages:

| Primary Need | Recommended Package | Alternate Options |
|---|---|---|
| **Persistent application data, SQL** | postgresql | mariadb (for MySQL compatibility) |
| **Embedded SQL in your app** | sqlite | *(Already included in base install)* |
| **Document storage with rich queries** | erretdb | mongodb (Opt-in, SSPL licensed) |
| **In-memory cache** | alkey | memcached, edis (Opt-in, RSAL/SSPL licensed) |
| **Embedded KV store in your app** | leveldb | ocksdb (High-performance, complex) |
| **Time-series data (metrics, events)** | influxdb3 | - |
| **Distributed cluster coordination** | tcd | - |
| **Wide-column distributed at scale** | cassandra | - |
| **Document database with HTTP/JSON API**| couchdb | - |

## 2. Category Overview

### Relational SQL (postgresql, mariadb, sqlite)
If you're building a web app and want a traditional, robust SQL database that handles concurrent connections and transactions cleanly, choose **PostgreSQL**. If your existing tooling strictly expects a MySQL-like backend, choose **MariaDB**. If you are building a standalone desktop application or a small script that needs a self-contained local database file, use **SQLite** (which is already present on your system).

### Document NoSQL (erretdb, mongodb, couchdb)
If you have unstructured JSON-like data and want to query it flexibly, you need a document store. We recommend **FerretDB**, which acts as a drop-in open-source replacement for MongoDB workloads by translating queries to a PostgreSQL backend. If you explicitly require the official MongoDB Community edition, it is available as an opt-in installation. **CouchDB** is an alternative document store that excels when you need a built-in HTTP/JSON API and strong multi-master replication features.

### In-Memory Cache (alkey, edis, memcached)
If you need to temporarily cache session data or frequent query results to dramatically speed up your application, an in-memory Key-Value store is required. We recommend **Valkey**, a high-performance open-source fork of Redis. **Memcached** is a simpler, highly effective alternative for pure string caching. If you strictly require official Redis (7.4+), it is available as an opt-in installation.

### Embedded Key-Value (leveldb, ocksdb)
If you are developing software in C/C++ or Rust and need to embed a lightning-fast, persistent key-value store directly into your application binary without running a separate server process, choose **LevelDB** for stability and simplicity, or **RocksDB** for maximum performance and highly concurrent SSD workloads.

### Specialized Workloads (influxdb3, tcd, cassandra)
- **InfluxDB 3 Core**: Choose this if you are recording thousands of timestamped data points per second (like server metrics or IoT sensor data). 
- **etcd**: Choose this if you are building distributed systems (like Kubernetes clusters) and need a reliable, highly available key-value store to manage configuration and coordinate nodes.
- **Apache Cassandra**: Choose this if you are building a massive, horizontally scalable system where you need to write data extremely fast across multiple datacenters with no single point of failure.

## 3. Licensing Transparency

InterGenOS is committed to open-source software. Most of the 14 database packages are OSI-approved FOSS (licensed under BSD, Apache, GPL, etc.).

However, two packages—**MongoDB Community** (SSPL) and **Redis 7.4+** (RSAL/SSPL)—ship under non-OSI licenses. InterGenOS includes them strictly as opt-in packages. When you attempt to install them, pkm will display a licensing banner to ensure you knowingly accept their terms before proceeding.

For these non-OSI packages, we provide FOSS-clean, wire-compatible alternatives:
- We recommend **FerretDB** over MongoDB.
- We recommend **Valkey** over Redis.

Use the FOSS alternatives by default unless your specific operational requirements demand the proprietary licenses.

## 4. Installation and Setup

Installing a database is as simple as running:
\\\ash
sudo pkm install postgresql
\\\

If you attempt to install an opt-in package with a restrictive license, you will be prompted:
\\\	ext
$ sudo pkm install redis
[NOTICE] The 'redis' package is licensed under the non-OSI RSAL/SSPL.
We recommend the open-source 'valkey' package as a wire-compatible alternative.
Do you accept the Redis license terms and wish to proceed? [y/N]
\\\

**Data Paths & Networking:**
- Database data files land in standard paths (typically /var/lib/<database>).
- By default, server databases are configured to bind *only* to 127.0.0.1 (localhost). They will not listen on public network interfaces until you deliberately edit their configuration files to allow it.

## 5. Security Defaults

In accordance with our "Security-Only Alignment" doctrine, every database server ships with aggressive hardening out-of-the-box:

- **Localhost Only**: Binds to 127.0.0.1 to prevent accidental public exposure.
- **Mandatory Authentication**: Where supported, databases are installed with authentication enforced and default random passwords generated during setup (rather than shipping with blank passwords).
- **TLS Prepared**: Directories and configurations for TLS certificates are pre-staged, though you must supply the certificates to activate encryption.
- **AppArmor Confined**: Each database service runs under a strict AppArmor profile in nforce mode, limiting what files and capabilities the process can access if compromised.
- **Systemd Hardening**: Services use strict systemd isolation directives (e.g., ProtectSystem=strict, PrivateTmp=yes) to sandbox the execution environment.

## 6. Further Reading

- Need to understand how pkm verifies these packages before installing? See the [Repository Trust Model](../repository-trust.md).
- Brand new to InterGenOS? Check out the [Getting Started Guide](../getting-started.md).
- For deep technical details on how the database tier was designed and packaged, read the maintainer-focused [Database Landing Plan](../architecture/database-landing-plan.md).
