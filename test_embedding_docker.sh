#!/usr/bin/env bash
# Quick test script for embedding service Docker deployment
# Usage: ./test_embedding_docker.sh

set -e

echo "================================"
echo "Embedding Service Docker Test"
echo "================================"

cd "$(dirname "$0")"

echo ""
echo "[1] Building Docker image..."
docker-compose build liara-embedding

echo ""
echo "[2] Starting embedding container..."
docker-compose up -d liara-embedding

echo ""
echo "[3] Waiting for service startup..."
sleep 5

echo ""
echo "[4] Testing health endpoint..."
curl -s http://localhost:8030/health | python3 -m json.tool | head -20

echo ""
echo "[5] Testing embedding generation..."
curl -s -X POST http://localhost:8030/embedding/generate \
  -H "Content-Type: application/json" \
  -d '{"input_text":"Hello world","normalize":true}' \
  | python3 -m json.tool | head -20

echo ""
echo "✓ Docker embedding service test complete!"
echo ""
echo "To stop: docker-compose down liara-embedding"
