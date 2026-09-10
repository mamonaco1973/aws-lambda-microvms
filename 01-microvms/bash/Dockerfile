FROM public.ecr.aws/lambda/microvms:al2023-minimal
# python3 runs the HTTP server; bash is the session runtime; jq frames the
# worker protocol. Nothing else -- every package here is snapshot weight paid
# on every image, and this runtime exists to show that the hook contract needs
# no SDK, not to be a full shell environment.
#
# Do NOT add coreutils: the base image ships coreutils-single, the two conflict,
# and dnf fails the build. sha256sum, head, tr, cut and mktemp all come from
# coreutils-single already.
RUN dnf install -y python3 jq && dnf clean all
WORKDIR /app
COPY server.py worker.sh ./
RUN chmod +x worker.sh
EXPOSE 8080 8081
CMD ["python3", "server.py"]
