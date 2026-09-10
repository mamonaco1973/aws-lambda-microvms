FROM public.ecr.aws/lambda/microvms:al2023-minimal
RUN dnf install -y python3 && dnf clean all
WORKDIR /app
COPY server.py worker.py ./
EXPOSE 8080 8081
CMD ["python3", "server.py"]
