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
