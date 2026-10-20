# Recorded hardware evidence

Recorded I2C transfers and acceptance summaries from the test box.
`test-evidence.py` uses the traces.

- `leopard-baseline-raw/`: the original 32.4.3 system rebinding its link driver
  and streaming each camera.
- `jp5-mux-reinit.trace`: R35 link setup, with the DMA-safe transfer flag.
- `leopard-boot.trace`: an original boot with failed transfers, used as a
  negative fixture.
- Acceptance summaries from the first bridge driver, which fully reset each
  deserializer at every start:
  `jp5-full-reset-warm1-acceptance.log` and `-warm2-` after reboots,
  `-pmic3-` after a module reset through its PMIC, and `-cli-` after link setup
  with the `leopard-gmsl` tool (raw capture only).
- `six-camera-tests.log`: start messages and acceptance results of the
  current driver after hard and normal reboots, and with a test build that
  fails pair 0 on purpose; forced full-reset trials with the `leopard-gmsl`
  tool; also a cold start and reboots with the version before, which allowed
  20 resets.
- `fallback-tests.log`: that version's start messages and capture results
  with two pairs, after forced fallback trials, normal reboots and hard
  reboots.
- `module-resets.log`: start messages and capture results of the driver
  before the full-reset fallback, after six module resets through the PMIC,
  with pairs 0 and 1 on link A before each reset and pair 2 not answering.
- `reset-review-20261020.json`, `-events.jsonl` and `-acceptance.log`: the
  camera control output, full reset and `REG1` experiments in
  [When a deserializer stops answering](../docs/porting-notes.md#when-a-deserializer-stops-answering),
  with the four-camera acceptance run afterwards. The other attempts listed
  there were not recorded here.
- `jp5-runtime-check.txt`: CUDA, cuDNN and TensorRT loaded on the box.

The traces keep only timestamped I2C events on adapters 30–32, with order,
payloads and results unchanged. Process and CPU fields, other buses, regmap
events and trace markers were removed. Acceptance summaries keep the result
lines.
