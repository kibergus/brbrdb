# Telemetry Data Compression Analysis & Benchmarks

This document records the empirical research and benchmarks conducted on compressing karting telemetry channel data for the Telemetry Viewer (`/api/telemetry/channel`).

## Benchmark Dataset
- **Session**: Whilton Mill (2026-10-03, Practice)
- **File**: `10_17_practice_katia_guseinova.csv`
- **Rows**: 179,511 samples (~30 minutes of telemetry at ~100 Hz)
- **Uncompressed Binary Payload**: 359,038 bytes (16-byte header + 179,511 `int16_t` deltas)

---

## 1. Compressor Levels Benchmark (Brotli vs Zstandard)

We benchmarked spending more CPU on the standard binary payload (16-byte header + 179,511 `int16_t` deltas):

| Compressor & Level | Compressed Size | Compression Ratio | CPU Time (per channel) | Browser Compatibility |
| :--- | :--- | :--- | :--- | :--- |
| **Raw Uncompressed** | 359,038 B | 100.0% | 0.0 ms | N/A |
| **Brotli Quality 4** | 275,008 B | 76.6% | **2.0 ms** | Universal (`Content-Encoding: br`) |
| **Brotli Quality 6** | 272,592 B | 75.9% | 4.0 ms | Universal |
| **Brotli Quality 9** | 271,173 B | 75.5% | 22.4 ms | Universal |
| **Brotli Quality 11** | **227,170 B** | **63.3%** | **595.7 ms** | Universal |
| **Zstandard Level 3** | 275,341 B | 76.7% | 0.9 ms | Modern Chrome/Firefox |
| **Zstandard Level 9** | 270,494 B | 75.3% | 3.2 ms | Modern Chrome/Firefox |
| **Zstandard Level 19** | 266,702 B | 74.3% | 21.8 ms | Modern Chrome/Firefox |
| **Zstandard Level 22** | 266,702 B | 74.3% | 23.5 ms | Modern Chrome/Firefox |

### Findings:
- **Brotli Q4 to Q9**: Gaining only 1.4% smaller file size takes 10x more CPU (22 ms vs 2 ms).
- **Brotli Q11**: Gains 17.4% smaller file size, but takes ~600 ms of CPU per channel. For 24 channels in parallel, this consumes ~14 CPU-seconds.
- **Zstandard**: Very fast at level 3-9, but compression ratio is comparable to Brotli, and browser support is less universal than Brotli.

---

## 2. Shannon Entropy & Compression Limits

To understand why lossless compressors hit a wall around ~220–270 KB, we computed the Shannon entropy of the 16-bit delta stream:

| Channel | 16-Bit Entropy | Theoretical Shannon Limit | Low Byte Entropy | High Byte Entropy |
| :--- | :--- | :--- | :--- | :--- |
| **Lat Force FL** | 10.64 bits / symbol | 233.1 KB | **7.99 bits / byte** | 2.80 bits / byte |
| **GForceLat** | 9.60 bits / symbol | 210.4 KB | **7.95 bits / byte** | 1.93 bits / byte |
| **GForceLon** | 8.49 bits / symbol | 186.0 KB | **7.77 bits / byte** | 1.33 bits / byte |

### Core Insight:
- In the 15-bit quantization formula (`scale = max_dev / 32760`), step size for tyre force is ~0.03 N.
- The lowest 8 bits of the `int16_t` delta stream have an entropy of **7.99 bits/byte**, which is effectively **pure high-frequency sensor rumble noise / white noise**.
- **No lossless compression algorithm can compress white noise beyond its entropy.** That is why Brotli Q11 stops at 227 KB (matching the theoretical limit of 233 KB).

---

## 3. Alternative Transformations

### Option A: Byte-Shuffle Filter (Transposition)
Because 16-bit deltas alternate low and high bytes (`[L0, H0, L1, H1, ...]`), the LZ77 match window crosses byte boundaries. By separating low and high bytes (`[L0, L1, ...] + [H0, H1, ...]`):
- **Brotli Quality 4 + ByteShuffle**: **235,746 B** (in **2.8 ms**).
- Achieves almost the same compression as Brotli-11 (235 KB vs 227 KB) while running **200x faster** (2.8 ms vs 595 ms).
- Requires a trivial 4-line de-shuffle in client JavaScript.

### Option B: 2nd-Order Delta Encoding (Delta of Deltas)
Encoding $d_2[i] = d_1[i] - d_1[i-1]$:
- **Brotli Quality 11 + 2nd-Order Delta**: **219,115 B** (down from 275 KB, 20.3% reduction).
- Requires double cumulative summation on client decode.

### Option C: Precision-Aware Quantization
Adjusting quantization levels to match display resolution (screen height ~250 pixels):

| Quantization Levels | Force Resolution | Speed Resolution | Brotli-Q4 Size | Brotli-Q11 Size | Gain vs Baseline |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **32,760 (15-bit, current)** | 0.030 N | 0.005 km/h | 275 KB | 227 KB | Baseline |
| **8,192 (13-bit)** | 0.121 N | 0.020 km/h | 227 KB | 190 KB | -17% |
| **4,096 (12-bit)** | 0.241 N | 0.040 km/h | 201 KB | 171 KB | -27% |
| **2,048 (11-bit)** | 0.482 N | 0.080 km/h | 174 KB | 148 KB | -37% |
| **1,024 (10-bit)** | 0.965 N | 0.160 km/h | 148 KB | 124 KB | -46% |
| **512 (9-bit)** | 1.930 N | 0.320 km/h | 124 KB | 98 KB | -55% |

- **Zero Client Changes**: Because `mean` and `scale` are stored in the 16-byte header, changing quantization levels requires **0 changes** to the client JavaScript decoder.

---

## 4. 12-Bit Packing vs 16-Bit Container Alignment Paradox

We investigated whether storing 12-bit quantized values in a 16-bit container wastes space compared to tight bit-packing (2 values in 3 bytes):

| Format | Uncompressed Size | Brotli-Q4 Compressed Size | JS Decoder Speed |
| :--- | :--- | :--- | :--- |
| **Tightly bit-packed 12-bit** (2 values / 3 bytes) | 269,268 B (-25%) | **207,817 B** *(Worst!)* | Slow (manual JS bit-shifts) |
| **Padded 16-bit container** (aligned `int16_t`) | 359,022 B | **201,333 B** *(Better)* | **Fast (native `Int16Array`)** |
| **Byte-shuffled 16-bit container** | 359,022 B | **193,835 B** *(Best)* | **Fast** |

### Why 16-bit Alignment Compresses Better:
1. **LZ77 Byte Boundaries**: Tight bit-packing shifts bits across byte boundaries depending on odd/even index positions, breaking repeating byte patterns for LZ77.
2. **Entropy Coder Handles Padding**: In 16-bit signed deltas, the upper 4 bits are sign extensions (`0x00` or `0xFF`). The entropy/Huffman coder encodes these long runs down to ~0.1 bits per byte. The 4 bits of padding are effectively eliminated for free during compression.
3. **Client Performance**: 16-bit containers allow native typed array decoding (`new Int16Array(...)`) in 1-2 ms without custom bit-unpacking loops in JavaScript.
