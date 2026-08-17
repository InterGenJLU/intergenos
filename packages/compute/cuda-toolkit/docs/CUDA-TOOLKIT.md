# The CUDA toolkit on InterGenOS

`cuda-toolkit` is an **installer**, not the toolkit. Installing the package puts
one script on the machine — `/usr/bin/igos-install-cuda-toolkit` — and running
it (which `pkm install cuda-toolkit` does for you) downloads NVIDIA's CUDA
toolkit and lays it out under `/opt/cuda`.

## Why it works that way

InterGenOS builds what it ships from source and publishes the result on its own
mirror. It cannot do that with CUDA. `nvcc`, NVIDIA's compiler, may not be
redistributed under NVIDIA's CUDA end user licence agreement, so a mirror
package carrying the toolkit would be redistributing something we have no right
to redistribute. Rather than read the licence narrowly and hope, the toolkit is
fetched from NVIDIA, on your machine, by a script you can read.

The same pattern already carries the other proprietary software the distribution
does not own: Chrome, Visual Studio Code, Claude Code.

## What you can check

- **The script is on your disk before it runs anything.** Read
  `/usr/bin/igos-install-cuda-toolkit`. It contains the exact download URL and
  the SHA-256 of the file it expects.
- **The download is verified before it is unpacked.** A hash mismatch refuses
  the install outright rather than continuing.
- **NVIDIA's installer is never run.** The runfile is a self-extracting shell
  archive, so unpacking it does run its outer wrapper — but only in the
  extract-only mode that writes the payload to a directory and stops, and only
  after the hash check above has passed. The installer inside it never runs:
  it would offer to replace this machine's GPU driver, and its silent mode
  accepts the licence on your behalf.
- **The licence text you agreed to is kept.** It is written to
  `/var/lib/intergen/legal/cuda-toolkit-<version>-EULA.txt` and to
  `/opt/cuda/EULA.txt`, alongside a JSON record of when it was accepted and by
  whom.
- **pkm knows what was installed.** Every file placed under `/opt/cuda` is
  recorded, so `pkm files cuda-toolkit`, `pkm verify cuda-toolkit` and
  `pkm remove cuda-toolkit` all operate on the real install.

## What is deliberately not installed

| Left behind | Why |
|---|---|
| The NVIDIA driver bundled inside the runfile (`NVIDIA-Linux-x86_64-*.run`) | InterGenOS ships its own `nvidia` package, built from the open kernel modules and signed with this machine's own key so it loads under enforced module signature verification. NVIDIA's bundled driver would fight that. |
| `cuda-uninstaller`, `ko-uninstaller` | They uninstall an installation this helper never performs. `pkm remove cuda-toolkit` is the removal path. |

Everything else NVIDIA's own `--toolkit` install lays down is installed, in the
same layout, so NVIDIA's documentation describes what you have.

## Where things are

```
/opt/cuda/bin/nvcc            the compiler        (add /opt/cuda/bin to PATH)
/opt/cuda/include             headers
/opt/cuda/lib64               runtime libraries   (already on the loader path)
/opt/cuda/EULA.txt            the licence
/etc/ld.so.conf.d/cuda.conf   what puts lib64 on the loader path
```

`/opt/cuda` is a merge of the runfile's per-component trees, which is exactly
what NVIDIA's own toolkit installer produces.

## Space and time

About 4.1 GB is downloaded and about 6.7 GB ends up at `/opt/cuda`. The download
and the unpacked copy exist at the same time, so roughly 11.5 GB of working
space is needed while it runs. The helper checks for that space **before** it
starts downloading and stops with a plain message if there is not enough.

The working directory is `/var/tmp`, not `/tmp`: `/tmp` is a RAM-backed
filesystem here, and unpacking seven gigabytes into it would spend memory the
machine needs. Set `IGOS_CUDA_WORKROOT` to use somewhere else.

## Using it

```sh
sudo pkm install cuda-toolkit
export PATH=/opt/cuda/bin:$PATH
nvcc --version
```

For GPU-accelerated local inference you do not need to compile anything: install
`llama-cpp-cuda`, which is built against this toolkit and links its runtime
libraries.

## Which GPUs

CUDA 13 supports Turing (GeForce RTX 20 series / GTX 16 series) and newer. That
is the same hardware floor as the `nvidia` driver package, which requires the
GSP firmware present from Turing onward. Older NVIDIA cards run on the in-kernel
`nouveau` driver and the Vulkan inference engine that ships by default.

## Removing it

```sh
sudo pkm remove cuda-toolkit
```

This removes the files under `/opt/cuda` that the helper recorded, and the
loader configuration. The acceptance record under `/var/lib/intergen/legal` is
left in place on purpose, so reinstalling the **same version** does not ask you
to accept a licence you have already accepted. The record names its version, so
a later toolkit version asks again — a different licence text deserves a fresh
answer.
