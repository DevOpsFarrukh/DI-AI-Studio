# Moving DI-AI-Studio from localhost to AWS

The app is one container: FastAPI, Piper and ffmpeg, with the voice models baked in. It needs no database, no GPU and no other AWS services. Every setting comes from environment variables, so the same image runs on a laptop, on EC2 or on ECS Fargate.

> ⚠️ **Before any company use:** the three demo voices (lessac, ryan, amy) are **not licensed for commercial use** (see README). Deploying to AWS doesn't change that. Swap the voices first (task T-012).

## Which AWS option?

| | **A. EC2 + docker compose + Caddy** (recommended to start) | **B. ECS Fargate + Application Load Balancer** |
|---|---|---|
| What runs | 1 virtual machine running the container and Caddy (automatic HTTPS) | Managed containers behind an AWS load balancer |
| HTTPS | Caddy + Let's Encrypt, automatic | AWS Certificate Manager (ACM) on the ALB |
| Effort | Lowest: the files in `deploy/` already work | More pieces: cluster, service, ALB, target group, IAM roles |
| Cost | 1 instance | Fargate task plus an always-on ALB |
| Restarts and patching | You maintain the VM (OS updates) | AWS manages the hosts |
| Scaling | Vertical (bigger instance) | Easy to add tasks later, but see the limits below first |

**Recommendation: start with A.** The app is single-instance by design today (see "Current limits"), so B's scaling doesn't help yet. Move to B when audio storage moves to S3.

## Sizing

- Speech generation is CPU-bound and runs one render at a time per container.
- On a 4-core laptop, 20 s of speech takes about 6 s to render.
- **Suggested:** 2 vCPU / 4 GB RAM. For example:
  - **c6i.large or c7i.large** on EC2, for steady CPU.
  - **t3.medium** for light testing only. Burstable CPU slows down once its credits run out.
  - **2 vCPU / 4 GB** on Fargate.
- **Architecture:** x86_64. The push script builds `linux/amd64`.
- **Disk:** at least 20 GB. The image is about 1 GB, and the app keeps the last 200 renders.

## Build, test and push the image (both options)

```bash
# 1. Build and test locally; the test stage runs all tests inside the image, real audio included
docker build --target test -t di-tts:test .
docker build -t di-tts .
docker run --rm -p 8000:8000 -e DI_TTS_API_KEY=local-test di-tts
deploy/smoke-test.sh http://127.0.0.1:8000 local-test

# 2. Push to Amazon ECR (creates the repository on first run; tag = git commit)
AWS_REGION=eu-central-1 deploy/aws/push-to-ecr.sh
```

## Option A: EC2 + docker compose + Caddy

1. **Launch an instance:** Ubuntu 22.04 or Amazon Linux 2023, 2 vCPU / 4 GB, 20+ GB disk.
   - Attach an IAM instance role with **AmazonEC2ContainerRegistryReadOnly**, so it can pull from ECR without stored keys.
2. **Security group:**
   - Inbound 443 and 80 from anywhere (or only your company's IP range).
   - 22 only from your IP. Better still, use SSM Session Manager and close 22.
   - Port 8000 is **not** opened; only Caddy is exposed.
3. **DNS:** point an `A` record (for example `tts.yourcompany.com`) at the instance's **Elastic IP**, so the IP survives restarts.
4. **On the instance:** install Docker and the compose plugin, then:
   ```bash
   git clone git@github.com:DevOpsFarrukh/DI-AI-Studio.git && cd DI-AI-Studio   # private repo: use a deploy key
   cp deploy/.env.example deploy/.env    # set DOMAIN, a long random DI_TTS_API_KEY, and TTS_IMAGE=<ECR image URI>
   aws ecr get-login-password --region <REGION> | docker login --username AWS --password-stdin <ACCOUNT_ID>.dkr.ecr.<REGION>.amazonaws.com
   docker compose -f deploy/docker-compose.yml --env-file deploy/.env pull tts
   docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --no-build
   deploy/smoke-test.sh https://tts.yourcompany.com "<API_KEY>"
   ```
   Without ECR, leave `TTS_IMAGE` empty and use `up -d --build` to build on the instance.
5. **Update to a new version:** push a new image, set `TTS_IMAGE` to the new tag, then run `pull` and `up -d --no-build` again.

## Option B: ECS Fargate + ALB

1. **Store the API key** as a SecureString in SSM Parameter Store:
   ```bash
   aws ssm put-parameter --name /di-ai-studio/api-key --type SecureString --value "<long random string>"
   ```
   The task execution role needs `ssm:GetParameters` on that parameter, plus the usual ECR and CloudWatch Logs permissions (`AmazonECSTaskExecutionRolePolicy`).
2. **Task definition:** fill in `<ACCOUNT_ID>`, `<REGION>` and `<TAG>` in [deploy/aws/ecs-task-definition.json](../deploy/aws/ecs-task-definition.json), then register it:
   ```bash
   aws ecs register-task-definition --cli-input-json file://deploy/aws/ecs-task-definition.json
   ```
3. **Target group:**
   - Target type `ip`, HTTP, port 8000.
   - Health check path `/api/health`, success code `200`.
   - The app returns **503** if a voice model or ffmpeg is missing, so a broken image is never put into service.
4. **ALB listeners:**
   - 443 with an **ACM** certificate, forwarding to the target group.
   - 80 redirecting to 443.
   - Keep the default 60 s idle timeout. The container's keep-alive is 65 s (`DI_TTS_KEEPALIVE_S`), which avoids random 502s.
5. **Service:**
   - Fargate, **desired count 1**, in private subnets with a NAT gateway (or public subnets with a public IP) so it can pull from ECR.
   - Task security group allows 8000 **only from the ALB's security group**.
6. **Smoke test:** `deploy/smoke-test.sh https://<alb-or-domain> "<API_KEY>"`.

## Configuration reference (environment variables)

| Variable | Default (container) | Purpose |
|---|---|---|
| `DI_TTS_API_KEY` | unset | **Set this on AWS.** Every `/api/*` call then needs `X-API-Key`. Users enter it under Settings. The server logs a warning if it's unset |
| `DI_TTS_MAX_CHARS` | 5000 | Max characters per request |
| `DI_TTS_KEEP_FILES` | 200 | Renders kept on disk (older ones are deleted) |
| `DI_TTS_KEEPALIVE_S` | 65 | Must exceed the load balancer's idle timeout |
| `FORWARDED_ALLOW_IPS` | `*` | Trust `X-Forwarded-*` from the proxy or ALB (the container only runs behind one) |
| `DI_TTS_LOG_LEVEL` | info | Logs go to stdout: CloudWatch on ECS, `docker logs` on EC2 |
| `DI_TTS_HOST` / `DI_TTS_PORT` | 0.0.0.0 / 8000 | Bind address |

## Current limits (by design, for now)

1. **Run exactly one container.** Audio files are stored on the container's own disk. With two or more tasks behind a load balancer, an audio link can land on the wrong task and return 404. Scaling out needs S3 storage for audio first.
2. **One render at a time per container.** Long scripts block shorter requests. A job queue is in the backlog.
3. **Renders are temporary.** On Fargate they vanish when the task restarts. On EC2 they're kept in a Docker volume. The history list lives in each user's browser, so old links show "Expired".
4. **Access control is one shared API key.** There are no user accounts yet. For company-wide use, put SSO in front (for example an ALB with Cognito or OIDC authentication) or add accounts.
5. **No rate limiting yet.** Keep the security group restricted to company IPs, or keep the API key private.

## Security checklist

- [ ] `DI_TTS_API_KEY` set from SSM or Secrets Manager, never written in a file in git.
- [ ] Only 443 (and 80 for the redirect) open to users; the app port is reachable only from Caddy or the ALB.
- [ ] HTTPS only (Caddy automatic, or ACM on the ALB).
- [ ] ECR scan-on-push enabled (the push script does this); review findings.
- [ ] The container runs as a non-root user (uid 10001). No AWS credentials inside the image.
- [ ] Voice licenses cleared for company use (T-012).
