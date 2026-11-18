# Agent Sandbox Deployment (E2B)

## Known issue: Embed template builds run out of disk space

On our self-hosted E2B (Embed), every template build step runs on a disk of about **800MB**. Our Python dependencies don't fit:

| | Size |
|---|---|
| Build disk | ~802MB |
| Already used (Python base image, apt packages, uv) | ~325MB |
| **Left for dependencies** | **~475MB** |
| Dependencies need | ~611MB |

The install fails with `No space left on device` while copying the Claude CLI that ships inside `claude-agent-sdk`. That package isn't the cause: installing it alone works. All the dependencies together are just too big for the space left.

### What didn't help

- **`min_free_disk_mb`**: only adds space *after* the build finishes, not during the build steps.
- **Raising the team's disk limit** in the E2B database: build steps don't use it.
- **Putting uv's cache in `/tmp`, or hardlinking**: these lower peak usage, but one copy of the dependencies still doesn't fit.

### Embed vs. E2B cloud

On E2B's cloud, the same template gets past the install step, so the disk limit is specific to Embed. We've reported it to the E2B team.

### Workaround

Build a Docker image with the dependencies already installed, then make the E2B template from that image. E2B sizes the disk to fit the image, so the limit doesn't apply.

```bash
docker build --platform linux/amd64 -f docker/e2b-agent-base.Dockerfile -t IMAGE .
docker push IMAGE
uv run --extra e2b python scripts/e2b_template.py --image IMAGE
```

See [Maintaining the Docker image workflow](#maintaining-the-docker-image-workflow) for the full process.

For code-only changes, `e2b_demo.py` can upload a fresh wheel, so the image only needs rebuilding when dependencies change.

## Maintaining the Docker image workflow

### How it fits together

- **Base image:** built from [docker/e2b-agent-base.Dockerfile](../docker/e2b-agent-base.Dockerfile) on the Embed VM. It contains Python, the locked dependencies, the package, `.claude/skills/`, the bundled Claude CLI, and a warm ENVO cache.
- **Registry:** a private `registry:2` container on the Embed VM, at `<VM_IP>:5000` (currently `10.138.0.4:5000`). It's only reachable inside the GCP network, so the image never leaves the VM. We don't use Artifact Registry because we don't have permissions for it.
- **E2B template:** `nmdc-agent`, built by [scripts/e2b_template.py](../scripts/e2b_template.py) from the base image. Sandboxes use `nmdc-agent:default`. If the build fails, that tag is missing, and `Sandbox.create` returns `404: tag 'default' does not exist`.

### When to rebuild

| Change | Rebuild image + template? |
|---|---|
| Code in `src/` only | No: `e2b_demo.py` uploads a fresh wheel each run |
| `pyproject.toml` / `uv.lock` (dependencies) | Yes |
| `.claude/skills/` | Yes: skills are baked into the image |
| Dockerfile, or the template's env/workdir | Yes |

### Rebuilding

1. **On the Embed VM**, in the repo checkout (`~/nmdc-metadata-suggestor-ai-tool`), build and push with a **new tag**:
   ```bash
   cd ~/nmdc-metadata-suggestor-ai-tool && git pull
   VM_IP=$(hostname -I | awk '{print $1}')
   TAG=v2   # bump each rebuild
   docker build -f docker/e2b-agent-base.Dockerfile -t $VM_IP:5000/nmdc-agent-base:$TAG .
   docker push $VM_IP:5000/nmdc-agent-base:$TAG
   curl http://$VM_IP:5000/v2/nmdc-agent-base/tags/list   # confirm the tag is there
   ```
   Use a new tag each time instead of reusing one, so E2B doesn't keep building from a stale cached image.
2. **On your machine**, rebuild the template:
   ```bash
   uv run --extra e2b python scripts/e2b_template.py --image 10.138.0.4:5000/nmdc-agent-base:$TAG
   ```
   It should end with `built template 'nmdc-agent': ...`.
3. **Smoke test:**
   ```bash
   uv run --extra e2b python scripts/e2b_demo.py --template nmdc-agent
   ```

### One-time VM setup (already done; redo if the VM is replaced)

```bash
# private registry, restarts with Docker
docker run -d --restart=always -p 5000:5000 --name registry registry:2

# allow Docker to push to it over plain HTTP
VM_IP=$(hostname -I | awk '{print $1}')
echo "{\"insecure-registries\": [\"$VM_IP:5000\"]}" | sudo tee /etc/docker/daemon.json
sudo systemctl restart docker
cd ~/e2b && docker compose up -d   # bring E2B back after the Docker restart
```

### Troubleshooting

- **`curl` to `10.138.0.4:5000` hangs from your laptop:** that's expected, since the IP is only reachable inside GCP. Run registry checks on the VM.
- **`404: tag 'default' does not exist`:** the last template build failed. Check its output. On the VM, `docker compose logs | grep <build id>` shows the failing step.
- **`No space left on device` during a build:** the template is installing dependencies in E2B build steps (the `--no-docker` path) instead of using the image. Pass `--image` and leave out `--no-docker`.
- **Registry gone after a reboot:** check `docker ps | grep registry`. Images are stored in the container's volume, so recreating the container with a new volume means you have to push again.
- **Disk on the VM:** old tags accumulate. Check with `docker system df` and remove unused local images with `docker image prune`.

### Security

- Don't make the registry reachable outside the VPC. Keep port 5000 closed in the GCP firewall.
- Every sandbox can read everything in the image. [.dockerignore](../.dockerignore) keeps `.env` and key files out. Build from a clean checkout.

## Current status

| Item | Status |
|---|---|
| Docker image workflow on Embed | ✅ Working, and the approach we're using |
| `--no-docker` build on Embed | ❌ Blocked by the ~800MB build disk |
| `--no-docker` build on E2B cloud | ⚠️ Install step passes; a later step failed because it used the system Python. Fixed, needs a rerun |
| Bug report to E2B | ✅ Submitted |

### Next steps

- [ ] Hear back from E2B on whether Embed's build disk size can be configured.
- [x] Try the Docker deployment: the template builds from `10.138.0.4:5000/nmdc-agent-base:v1`.
- [ ] Run `e2b_demo.py --template nmdc-agent` end to end on Embed.