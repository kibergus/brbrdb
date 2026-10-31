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

---

## 5. 8-Bit Overflow-Aware Delta Encoding & Slew Limiting

### 5.1 Offset as Initial Value ($v_0$)
Currently, `offset` is calculated as the arithmetic mean of all samples, and `deltas[0]` holds $(v_0 - \text{mean}) / \text{scale}$.
Setting `offset = values[0]` (the first valid sample, stored as Float64 in bytes 0..7 of the header) means:
- `deltas[0] = 0`
- Every delta $d_i$ ($i \ge 1$) becomes a pure step-change: $d_i = q_i - q_{i-1}$.
- Deltas are completely freed from encoding the absolute signal level.

### 5.2 The 16-Bit Delta Scaling Trap
If we keep 16-bit deltas and compute `scale = max(delta) / 32760`:
- For **Speed**, $\max(\Delta v) = 4.85\text{ km/h}$, yielding $\text{scale} = 4.85 / 32760 \approx 0.000148\text{ km/h}$.
- This creates excessive micro-precision (20x below sensor noise).
- The lowest 12+ bits become pure white noise, and **Brotli-Q4 compressed size explodes from 158 KB to 271 KB (+71% larger)**!
- **Conclusion**: We must NOT allocate 16 bits of precision to deltas.

### 5.3 8-Bit Overflow-Aware Delta Encoding
By packing deltas into `int8_t` ($-127 \dots 127$, with $-128$ as NaN):
1. **Raw Payload**: Halves instantly from 359 KB to **179.5 KB**.
2. **Resolution Constraint**: Target at least 2048 levels of the full channel span:
   $$\text{scale}_{2048} = \frac{\text{span}}{2048}$$
3. **Smearing / Catchup Dynamics**:
   - In 1 sample (10 ms), an 8-bit delta can jump at most $127 \times \text{scale}_{2048} \approx \frac{\text{span}}{16.1}$ (~6.2% of span).
   - In 4 samples (40 ms), it can jump at most $4 \times 127 \times \text{scale}_{2048} = 508 \times \text{scale}_{2048} \approx \frac{\text{span}}{4.03}$ (~25% of span).
   - To guarantee catching up with **any** abrupt jump $\max(\Delta v)$ in at most 4 samples:
     $$\text{scale} = \max\left(\frac{\text{span}}{2048},\; \frac{\max(\Delta v)}{508}\right)$$
   - When a step change exceeds $127 \times \text{scale}$ (e.g. stamping the throttle/brake), the encoder clamps deltas to $\pm 127$, creating a controlled slew rate limit over 2–4 samples (20–40 ms) until tracking converges.

---

### 5.4 Empirical Benchmarks (Whilton Mill, 179,511 samples)

| Channel | Span / Max $\Delta v$ | Baseline (16-bit) Br-Q4 | 8-Bit (Catchup $\le 4$) Br-Q4 | Gain vs Baseline | Br-Q11 Size | RMSE / Max Error | Clamped Samples |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Speed** | 87.2 km/h / 4.85 km/h | 158,971 B | **46,958 B** | **-70.5%** | 37,966 B | 0.012 / 0.021 km/h | 0 (0.00%) |
| **Steering Angle** | 121.3° / 4.90° | 108,657 B | **88,416 B** | **-18.6%** | 76,938 B | 0.017 / 0.030° | 0 (0.00%) |
| **Throttle** | 96.9% / 45.5% | 32,414 B | **20,952 B** | **-35.4%** | 17,209 B | 1.31 / 34.1% | 1,709 (0.95%) |
| **Brake** | 100.0% / 100.0% | 7,670 B | **3,861 B** | **-49.7%** | 3,369 B | 0.31 / 75.0% | 10 (<0.01%) |
| **GForceLat** | 19.3 g / 14.8 g | 243,574 B | **79,294 B** | **-67.4%** | 67,384 B | 0.033 / 11.1 g | 3 (<0.01%) |
| **Lat Force FL** | 3674 N / 2325 N | 271,628 B | **99,674 B** | **-63.3%** | 90,279 B | 15.1 / 1744 N | 383 (0.21%) |

*(Note: For Throttle and Brake, zero-overflow encoding with $\text{scale} = \max(\Delta v)/127$ provides 270 levels [0.37% resolution] and 127 levels [0.79% resolution] respectively with **zero smearing error**, compressing to **15,941 B** [-50.8%] and **2,721 B** [-64.5%]).*

---

### 5.5 Key Takeaways & Client Impact

1. **Overall Compression Gain**: Total compressed payload across standard channels drops by **50% to 70%** (e.g. Speed drops from 159 KB to 47 KB; Tyre Force drops from 271 KB to 99 KB).
2. **Smooth Channels (Speed, Steering)**: Naturally never exceed 127 levels per 10ms sample. Zero clamping, perfect fidelity, and dramatic file size reduction.
3. **Step Inputs (Pedals)**:
   - Slew limiting across 4 samples takes 40 ms at 100 Hz.
   - Alternatively, allocating 270 levels (8-bit zero overflow) preserves instant step response with zero error while delivering even higher compression.
4. **Client-Side Changes Required**:
   - Client JS decoder changes from `new Int16Array(buffer.slice(16))` to `new Int8Array(buffer.slice(16))`.
   - Reconstructed value logic remains identical: `currentQuantized += deltas[i]; values[i] = v0 + currentQuantized * scale;`.

