FROM public.ecr.aws/lambda/microvms:al2023-minimal
RUN dnf install -y nodejs && dnf clean all
WORKDIR /app
COPY server.js worker.js ./
EXPOSE 8080 8081
CMD ["node", "server.js"]
