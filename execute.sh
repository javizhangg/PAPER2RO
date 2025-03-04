#!/bin/bash

echo "⏳ Waiting for Grobid to be ready..."

echo "The program will run in 90 seconds"
sleep 90

echo "✅ Grobid is running. Starting the application..." 

# Use conda run to execute the script within the Conda environment
conda run --no-capture-output -n mi_entorno python main.py

echo "Running a few tests to verify that the outputs are correct"
conda run --no-capture-output -n mi_entorno python test.py

echo "✅ Execution completed. Shutting down the process."

# Terminate the script and stop execution
exit 0
