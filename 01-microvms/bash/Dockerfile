FROM public.ecr.aws/lambda/microvms:al2023-minimal
# python3 runs the HTTP server; bash is the session runtime. jq frames the
# worker protocol, and procps-ng lets a cell inspect its own background jobs
# across a suspend -- the one thing this runtime demonstrates better than the
# other two.
RUN dnf install -y bash python3 jq procps-ng coreutils && dnf clean all
WORKDIR /app
COPY server.py worker.sh ./
RUN chmod +x worker.sh
EXPOSE 8080 8081
CMD ["python3", "server.py"]
