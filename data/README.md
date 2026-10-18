# Benchmark data

- `README.md`: tracked dataset sources and setup instructions.
- `fixtures/`: tracked, small synthetic cases for offline examples and tests.
- `ifbench/`: git-ignored downloaded dataset, excluded from the Python package.

`ifbench/IFBench_test.jsonl` holds the local, single-turn IFBench dataset.
Downloaded data stays outside git and the Python wheel. No upstream evaluator
code or dependencies are needed.

Download the pinned source explicitly:

```bash
mkdir -p data/ifbench
curl --fail --location --output data/ifbench/IFBench_test.jsonl \
  https://raw.githubusercontent.com/allenai/IFBench/1c40f0c10d9b5c5c2f10a175a28007ebb64f7f4d/data/IFBench_test.jsonl
shasum -a 256 data/ifbench/IFBench_test.jsonl
```

Expected SHA-256:
`d2ada7da94a38cfe406351614c4e686846ed2da6d1b339db95fa5ead19554a4a`.
The adapter verifies this before running any target.

Source: [AllenAI IFBench](https://github.com/allenai/IFBench).
Consult the [dataset card](https://huggingface.co/datasets/allenai/IFBench_test)
for licensing, attribution, citation, and use conditions. Dataset integration
does not imply official-score compatibility. Choose and name your scorer;
unsupported constraints must remain explicitly unsupported.

For offline API demonstrations, `data/fixtures/IFBench_test.jsonl` contains one
synthetic case with its own checksum. It is not part of the official dataset.
Adapter tests use this fixture. `examples/ifbench.py` separately benchmarks real
`unmask.adherence` using three manually annotated JSON-format cases defined in
the script. Those cases are synthetic, not converted from the downloaded dataset.
