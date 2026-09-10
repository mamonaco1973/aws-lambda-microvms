FROM public.ecr.aws/lambda/microvms:al2023-minimal
# python3 runs the HTTP server; bash is the session runtime. jq frames the
# worker protocol, and procps-ng lets a cell inspect its own background jobs
# across a suspend -- the one thing this runtime demonstrates better than the
# other two.
#
# Do NOT add coreutils here: the base image ships coreutils-single, the two
# conflict, and dnf fails the build. sha256sum, head, tr, cut and mktemp all
# come from coreutils-single already.
RUN dnf install -y python3 jq procps-ng && dnf clean all
WORKDIR /app
COPY server.py worker.sh ./
RUN chmod +x worker.sh
EXPOSE 8080 8081
CMD ["python3", "server.py"]
