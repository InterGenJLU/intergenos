# Databases on InterGenOS

Every major open-source database category is available to install with `pkm install`. Apart from SQLite — which is part of the base system, because a great deal of desktop software embeds it — no database server is pre-installed, so you choose exactly what your workload needs.

InterGenOS provides curated database packages across relational SQL, in-memory key-value cache, embedded key-value, time-series, and distributed key-value categories.

`pkm search` matches text in a package's name and description, so `pkm search
database` finds the packages that use that word and misses the ones that
describe themselves as a key-value store — Valkey, Memcached, LevelDB, RocksDB
and etcd among them. It is a text search, not a category listing. The tables in
this guide name every database package the release carries; search for one by
name (`pkm search valkey`) to see its version and tier, and `pkm info valkey`
for its licence, size and checksum whether or not it is installed.

## 1. Quick Chooser

Not sure which one to pick? Match your primary use case to the recommended package:

| Primary Need | Recommended Package | Alternate Options |
|---|---|---|
| **Persistent application data, SQL** | postgresql | mariadb (for MySQL compatibility) |
| **Embedded SQL in your app** | sqlite | *(Already included in the base system)* |
| **In-memory cache** | valkey | memcached |
| **Embedded KV store in your app** | leveldb | rocksdb (High-performance, complex) |
| **Time-series data (metrics, events)** | influxdb | - |
| **Distributed cluster coordination** | etcd | - |

## 2. Category Overview

### Relational SQL (postgresql, mariadb, sqlite)
If you're building a web app and want a traditional SQL database that handles concurrent connections and transactions, choose **PostgreSQL**. If your existing tooling strictly expects a MySQL-like backend, choose **MariaDB**. If you are building a standalone desktop application or a small script that needs a self-contained local database file, use **SQLite**, which is already included in the base system.

### In-Memory Cache (valkey, memcached)
If you need to temporarily cache session data or frequent query results to dramatically speed up your application, an in-memory Key-Value store is required. We recommend **Valkey**, a high-performance open-source fork of Redis. **Memcached** is a simpler, highly effective alternative for pure string caching.

### Embedded Key-Value (leveldb, rocksdb)
If you are developing software in C/C++ or Rust and need to embed a lightning-fast, persistent key-value store directly into your application binary without running a separate server process, choose **LevelDB** for stability and simplicity, or **RocksDB** for maximum performance and highly concurrent SSD workloads.

### Specialized Workloads (influxdb, etcd)
- **InfluxDB 3 Core**: Choose this if you are recording thousands of timestamped data points per second, such as server metrics or IoT sensor data.
- **etcd**: Choose this if you are building distributed systems (like Kubernetes clusters) and need a reliable, highly available key-value store to manage configuration and coordinate nodes.

## 3. Licensing Transparency

InterGenOS is committed to open-source software. Every curated database package is OSI-approved and licensed under permissive or copyleft terms (BSD, Apache, GPL/LGPL, MIT, and the PostgreSQL license).

Where a popular database has moved to a restrictive non-OSI license, we ship an open-source, wire-compatible alternative instead. For example, **Valkey** is the recommended in-memory cache as a drop-in alternative to Redis.

If a future release ever includes a package under a non-OSI license, pkm will display a licensing notice when you attempt to install it, so you knowingly accept its terms before proceeding.

## 4. Installation and Setup

Installing a database is as simple as running:
```bash
sudo pkm install postgresql
```

For an in-memory cache, install Valkey the same way:
```bash
sudo pkm install valkey
```

**Data Paths & Networking:**
- Database data files land in standard paths (typically /var/lib/<database>).
- By default, server databases are configured to bind *only* to 127.0.0.1 (localhost). They will not listen on public network interfaces until you deliberately edit their configuration files to allow it.

## 5. Security Defaults

Security is not first. It is only. Every database server ships hardened by default:

- **Localhost Only**: Binds to 127.0.0.1 to prevent accidental public exposure.
- **Mandatory Authentication**: Where supported, databases are installed with authentication enforced and default random passwords generated during setup (rather than shipping with blank passwords).
- **TLS Prepared**: Directories and configurations for TLS certificates are pre-staged, though you must supply the certificates to activate encryption.
- **AppArmor Confined**: Each database service runs under a strict AppArmor profile in enforce mode, limiting what files and capabilities the process can access if compromised.
- **Systemd Hardening**: Services use strict systemd isolation directives (e.g., ProtectSystem=strict, PrivateTmp=yes) to sandbox the execution environment.

## 6. Further Reading

- Need to understand how pkm verifies these packages before installing? See the [Repository Trust Model](../repository-trust.md).
- Brand new to InterGenOS? Check out the [Getting Started Guide](../getting-started.md).
