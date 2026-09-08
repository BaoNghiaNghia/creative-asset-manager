# Visual Search Encoder Benchmark

This command is development-only. It does not change application configuration,
create an Elasticsearch index, queue jobs, or access production.

Run it with a pinned, isolated encoder factory only after its model license and
runtime have been reviewed:

    cd apps/api
    python -m app.operations.visual_search_benchmark \
      --encoder-factory package.module:create_encoder \
      --image /absolute/path/to/reference.jpg \
      --iterations 20

Capture the JSON output for each candidate and fixed revision. The minimum
comparison records cold-load time, current RSS after load, single encode latency,
20 sequential encode p50/p95, output dimension, preprocessing version and
similarity. Run the same image/crop fixture set for every candidate.

Do not install model dependencies into the CAM API or general worker
environment. Use an isolated development encoder environment for this benchmark.
No model is approved for production until relevance, memory and CPU results are
reviewed.


## Development technical baseline — 2026-09-08

The following measurements are development-only, on a WSL CPU environment with
PyTorch CPU 2.5.1, Transformers 4.46.3, one PyTorch thread and one synthetic
640×480 image. They are not a production capacity claim and are not a
relevance evaluation.

| Candidate | Pinned revision | Dimension | Cold load | RSS | Single | 20 sequential p50 / p95 |
|---|---|---:|---:|---:|---:|---:|
| google/siglip-base-patch16-224 | 7fd15f0689c79d79e38b1c2e2e2370a7bf2761ed | 768 | 359.2 ms | 756.5 MB | 359.0 ms | 316.8 / 327.2 ms |
| openai/clip-vit-base-patch32 | 3d74acf9a28c67741b2f4f2ea7635f0aaf6f0268 | 512 | 727.4 ms | 725.8 MB | 137.5 ms | 126.6 / 136.9 ms |

User decision on 2026-09-08 selects the pinned SigLIP revision as the V1
baseline. The relevance fixture benchmark is explicitly deferred, rather than
being used as a model-selection gate. It remains required before broad
production rollout. CLIP is not selected; its fixed-revision license was not
verified for this implementation.
