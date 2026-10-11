FROM public.ecr.aws/lambda/python:3.12

RUN dnf install -y nodejs npm && dnf clean all
WORKDIR ${LAMBDA_TASK_ROOT}

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY zk/package.json zk/package-lock.json ./zk/
RUN cd ./zk && npm ci --omit=dev
COPY zk ./zk
COPY ui ./ui
COPY app.py bedrock_workflow.py lambda_handler.py ./

ENV AGENTSAFE_AWS_NATIVE=1 \
    AGENTSAFE_DATA_DIR=/tmp/agentsafe \
    AGENTSAFE_ZK_DIR=/var/task/zk \
    NODE_BIN=/usr/bin/node

CMD ["lambda_handler.handler"]
