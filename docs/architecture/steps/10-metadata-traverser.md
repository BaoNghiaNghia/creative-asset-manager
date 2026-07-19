# Step 10 — Metadata traverser

## Contract

Each extracted scalar contains a normalized logical path, its lossless string
representation, and a string, number, or boolean type.

Array indexes are not part of logical identity. For example,
visual_entities[0].species and visual_entities[4].species both become
visual_entities.species.

## Safety and determinism

MetadataTraverser walks objects and arrays without mutating them. Mapping keys
are processed deterministically and final values are ordered by path, type, and
original value. Null and unsupported values are ignored. Boolean extraction is
opt-in.

Depth, node, array item, and extracted value limits bound work. Cyclic Python
objects are safely skipped even though validated JSON cannot contain cycles.

## Exclusions

Global exclusions protect URLs, credentials, tokens, base64, embeddings,
vectors, coordinates, bounding boxes, provider request IDs, raw/debug payloads,
and common compound-name variants. Profile exclude_paths add tenant/profile
specific subtree exclusions but cannot disable global safety exclusions.

This step adds no migration, route, worker, or runtime invocation.
