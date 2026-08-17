# The CUDA inference engine

`llama-cpp-cuda` is the same llama.cpp that InterGenOS already ships, compiled
against NVIDIA's CUDA toolkit instead of Vulkan. It is an **opt-in alternative**
to the engine you already have, not an upgrade you should assume is faster.

## Measure before you switch

On the machine this package was developed on — a GeForce RTX 3070 Ti Laptop
(Ampere, compute capability 8.6, 8 GB, driver 580.159.04) running a
9-billion-parameter model quantised to Q4_K_M — **the Vulkan engine that ships
by default was faster than CUDA on every measurement taken**, by three to six
and a half percent. Both engines were the same llama.cpp source, the same model
file, the same full-GPU offload, on an otherwise idle GPU from the same thermal
starting point. Measured 2026-08-04, tokens per second, three repeats each:

| | prompt, 512 tokens | prompt, 2048 tokens | generation, 128 tokens |
|---|---|---|---|
| Vulkan (default) | 1340.70 ± 30.86 | 1273.58 ± 28.11 | 36.38 ± 0.57 |
| CUDA (this package) | 1298.62 ± 36.28 | 1220.64 ± 4.83 | 34.00 ± 0.83 |

The likely reason is visible in the Vulkan engine's own startup line: this
card's NVIDIA Vulkan driver advertises `NV_coopmat2` cooperative-matrix
support, so the Vulkan path reaches the tensor cores too. On a card or driver
without that, the comparison would look different.

That was not the expected result — the project's own design notes assumed CUDA
would win prompt processing outright — which is exactly why this document leads
with "measure" rather than with a recommendation. One card, one model, one
quantisation. Measure yours.

Both engines install a benchmark tool, so the comparison takes two commands:

```sh
# the CUDA engine (this package)
/opt/llama-cpp-cuda/bin/llama-bench -m <model.gguf> -ngl 99 -p 512,2048 -n 128

# the Vulkan engine that ships by default
/usr/bin/llama-bench -m <model.gguf> -ngl 99 -p 512,2048 -n 128
```

`pp` rows are prompt processing (how fast the model reads what you send it);
`tg` rows are token generation (how fast it writes its reply).

Two things will mislead you if you skip them:

- **Nothing else may be using the GPU.** If the InterGen assistant is running,
  its model already holds most of your video memory and the benchmark will
  either crash or measure something else entirely. A plain `stop` is not enough
  to rely on: the login screen starts its own user session, which can start the
  assistant again while you are between benchmark runs, and the second run then
  measures a card that is already full. Hold it down for the whole comparison
  and put it back afterwards:

  ```sh
  systemctl --user mask --now intergen.service
  nvidia-smi --query-gpu=memory.used,memory.free --format=csv   # confirm it is free
  # ... run both benchmarks ...
  systemctl --user unmask intergen.service
  systemctl --user start intergen.service
  ```

  Check the free memory again immediately before *each* engine's run, not only
  once at the start.
- **Run each engine from the same thermal state.** On a laptop, whichever
  engine runs second starts hot and can measure several percent slower purely
  because of that. Let the GPU cool between runs, or run the comparison in both
  orders and compare like with like.

## When CUDA is likely to be the right choice anyway

- **Cards where the Vulkan driver is weak.** The comparison above is a result
  about one Ampere laptop card with a mature NVIDIA Vulkan driver. On other
  cards the gap can go either way.
- **Multiple GPUs.** Vulkan scales poorly across devices; CUDA is the better
  path when work is split over more than one card.
- **Tooling.** If you are profiling or debugging kernels, the CUDA toolkit's
  tools understand a CUDA build and not a Vulkan one.

If none of those apply and the numbers are close on your card, the engine that
ships by default is the simpler answer — it needs no extra download, no
proprietary toolkit, and no licence acceptance.

## What it needs

| Requirement | Where it comes from |
|---|---|
| CUDA runtime libraries (`libcudart`, `libcublas`, `libcublasLt`) | `pkm install cuda-toolkit` — fetched from NVIDIA at install time |
| CUDA driver library (`libcuda.so.1`) | `pkm install nvidia` — the driver package |
| Turing (RTX 20 / GTX 16 series) or newer GPU | CUDA 13 supports nothing older |

Installing this package pulls in both dependencies, but pkm will **not** run the
CUDA toolkit's download helper for you — that would mean accepting NVIDIA's
licence on your behalf. So after installing this, run:

```sh
sudo pkm install cuda-toolkit
```

Until you do, the engine's binaries will not start: they need libraries that are
not on the machine yet. The post-install message says so at the time, rather
than leaving you to decode a missing-library error later.

## Where it lives, and why it is not in `/usr/bin`

Everything is under `/opt/llama-cpp-cuda`:

```
/opt/llama-cpp-cuda/bin/llama-server
/opt/llama-cpp-cuda/bin/llama-cli
/opt/llama-cpp-cuda/bin/llama-bench
```

`/usr/bin/llama-server` stays the Vulkan engine, and only one package in the
whole distribution claims that path. This is deliberate. The default engine
loads its `libllama` and `libggml` libraries from `/usr/lib`; a second engine
installing libraries with those same names would make "which backend am I
actually running" depend on library search order — the kind of question that
gets answered wrong silently. This package sidesteps it completely: its copies
of those libraries are compiled *into* its binaries rather than installed
anywhere, so the two engines cannot interfere no matter what order anything
loads in.

It is also not installed into `/opt/cuda`. That directory belongs to the
`cuda-toolkit` package's download helper, which records every file it puts
there; putting our binaries in it would give one directory two owners and make
removal ambiguous.

## Running it

```sh
/opt/llama-cpp-cuda/bin/llama-server \
    --model /var/lib/intergen/models/llm/<model>.gguf \
    --n-gpu-layers 99 --port 8080
```

`--n-gpu-layers 99` puts every layer it can on the GPU. If the model does not
fit in video memory, lower it until it does — the layers that do not fit run on
the CPU, which is much slower but works.

## Which GPUs this build covers

Compiled kernels are included for the RTX 30, 40 and 50 series. Turing and the
Ampere data-centre parts are covered by portable intermediate code that the
driver compiles on first use — those cards work, with a one-off pause the first
time a model loads. The exact architecture list is in `package.yml` under
`gpu_targets`, with a line of explanation per entry.

## Licensing

Every byte of this package is compiled from llama.cpp's MIT-licensed source.
It contains no NVIDIA code: the CUDA libraries are loaded at run time from
`/opt/cuda`, not built in. The toolkit used to compile it is fetched from
NVIDIA at build time and is not redistributed by InterGenOS — the
corresponding-source archive for this package says so explicitly and gives
NVIDIA's own URL and the SHA-256 the build verified, so the build is
reproducible by anyone who accepts NVIDIA's licence themselves.
