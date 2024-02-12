#!/usr/bin/env bash

./build.sh

docker save hanseg2023algorithm_zhack | gzip -c > HanSeg2023AlgorithmZhack.tar.gz
