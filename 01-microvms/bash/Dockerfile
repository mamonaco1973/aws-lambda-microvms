FROM public.ecr.aws/lambda/microvms:al2023-minimal
# python3 runs the HTTP server; bash is the session runtime; jq frames the
# worker protocol. Nothing else -- every package here is snapshot weight paid
# on every image, and this runtime exists to show that the hook contract needs
# no SDK, not to be a full shell environment.
#
# Do NOT add coreutils: the base image ships coreutils-single, the two conflict,
# and dnf fails the build. sha256sum, head, tr, cut and mktemp all come from
# coreutils-single already.
RUN dnf install -y python3 jq nfs-utils amazon-efs-utils util-linux && dnf clean all
RUN command -v mount.s3files && command -v amazon-efs-mount-watchdog
WORKDIR /app
COPY server.py worker.sh storage.sh ./
RUN chmod +x worker.sh storage.sh
EXPOSE 8080 8081
CMD ["python3", "server.py"]
