# Optional Slurm adapter example

The optional Slurm adapter records an explicitly named execution and can poll a
safe, command-time target. It is not required for the dashboard core, is not
run automatically, and does not store a cluster target in dashboard state.

```bash
.venv/bin/research-dashboard slurm register \
  --execution-id example-run-001 \
  --job-id 12345 \
  --project-id example-climate-model \
  --task-key prepare-baseline

.venv/bin/research-dashboard slurm poll \
  --execution-id example-run-001 \
  --ssh-target user@cluster.example
```

Use only a numeric job ID and a safe host or `user@host` token. The examples
are synthetic and do not contact a scheduler.
