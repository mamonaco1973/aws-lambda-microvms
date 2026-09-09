# Demo Selection and AWS Capabilities

Research checked September 9, 2026. AWS announced Lambda MicroVMs on **June 22, 2026**. The service exposes finite-lived, explicitly controlled execution environments with initialized-image launch, suspend/resume and authenticated per-VM HTTPS endpoints. [Launch announcement](https://aws.amazon.com/about-aws/whats-new/2026/06/aws-lambda-microvms/)

The documented launch regions are N. Virginia, Ohio, Oregon, Ireland and Tokyo, with ARM64 images. Sessions can last up to eight hours. The selected minimum baseline is 512 MiB and 0.25 vCPU; allocations can burst. This demo uses a shorter 30-minute lifetime and does not benchmark burst behavior. [Developer guide](https://docs.aws.amazon.com/lambda/latest/dg/lambda-microvms-guide.html)

Images are built from a source archive containing a Dockerfile, initialized and snapshotted. Image and runtime lifecycle hooks allow readiness validation and per-session initialization after restore. Identity generated before the snapshot can be shared by clones, which is why the demo generates its session nonce in the run hook. [Images and hooks](https://docs.aws.amazon.com/lambda/latest/dg/microvms-images.html)

An idle policy can suspend a session and automatically resume it on authenticated HTTP traffic. Tokens target an individual VM and can restrict destination ports. Explicit lifecycle calls and `GetMicrovm` let the dashboard observe suspension without application traffic. [Launching and lifecycle](https://docs.aws.amazon.com/lambda/latest/dg/microvms-launching.html)

Networking includes authenticated HTTPS ingress and HTTP/2 protocols. PrivateLink support was announced August 25, 2026; this small public-endpoint demo does not need VPC endpoints. [Networking](https://docs.aws.amazon.com/lambda/latest/dg/microvms-networking.html), [PrivateLink announcement](https://aws.amazon.com/about-aws/whats-new/2026/08/lambda-microvms-supports-privatelink/)

## Candidate Demos

| Candidate | Technical evidence | Recording tradeoff |
|---|---|---|
| Interactive Python sessions | Interpreter continuity, mutable memory, generator position, files, tenant separation | Clear before/after and small dependencies; selected |
| CI runner | Fresh isolated execution and disposable builds | Build output dominates, little reason to suspend |
| Browser automation | Stateful browser processes across pauses | Large images, fragile external pages and more cost |
| AI coding agent | Separate execution environment for generated code | Model latency and nondeterminism obscure the compute behavior |

**Two interactive Python sessions are the strongest 10–15 minute demonstration.** The live generator is particularly useful: its next value changes after resume, without loading interpreter state from DynamoDB or replaying cells. A second session makes both independent state and independent lifecycle visible on one screen. Ordinary Lambda functions provide the short-lived controller, making the roles of each compute service concrete within the same project.

## Compute Comparison

| Service | Good fit | Relevant difference in this demonstration |
|---|---|---|
| Ordinary Lambda functions | Event-driven, bounded request processing | An application does not own a persistent interpreter between requests; warm reuse is not that contract |
| Lambda MicroVMs | Finite-lived interactive execution sessions | Explicit lifecycle, pre-initialized image, stateful suspend/resume and per-session endpoint |
| ECS / Fargate | Container services and long-running tasks | Fargate already provides hardware isolation; preserving live task memory ordinarily means keeping the task running |
| EC2 | Full instance control, custom networking and runtimes | Hibernation can preserve RAM, but instance/storage/network/session orchestration remains the application's responsibility |

Lambda tenant isolation also supports tenant-specific execution environments. **Isolation alone is not the differentiator.** SnapStart initialization snapshots and durable workflow state are also different from retaining the execution position of an arbitrary live interpreter. These comparisons are capability comparisons, not measured performance benchmarks. [Lambda execution lifecycle](https://docs.aws.amazon.com/lambda/latest/dg/lambda-runtime-environment.html), [Tenant isolation](https://docs.aws.amazon.com/lambda/latest/dg/tenant-isolation.html), [Fargate isolation](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/security-shared-model.html), [EC2 hibernation](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/Hibernate.html)

## Terraform

The image uses `hashicorp/aws`'s `aws_cloudcontrolapi_resource` with explicit JSON properties. This preserves the required empty `AdditionalOsCapabilities` and `EnvironmentVariables` arrays; the AWSCC resource drops empty sets during serialization. Cloud Control is the provider's API, not a CloudFormation stack deployment. The image's active version is passed into the controller's environment. [Terraform Cloud Control resource](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/cloudcontrolapi_resource), [AWSCC serialization implementation](https://github.com/hashicorp/terraform-provider-awscc/blob/main/internal/generic/translate.go)
