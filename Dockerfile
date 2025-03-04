# Use an official Miniconda image as the base
FROM continuumio/miniconda3:latest

# Set the working directory inside the container
WORKDIR /app

# Copy all project files into the container
COPY . .

# Install necessary system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Configure Conda and create the environment based on `environment.yml`
RUN conda config --set channel_priority strict && \
    conda env create -f environment.yml

# Switch to the `mi_entorno` environment
SHELL ["/bin/bash", "-c"]
RUN echo "source activate mi_entorno" >> ~/.bashrc
ENV PATH="/opt/conda/envs/mi_entorno/bin:$PATH"

# Ensure scripts have execution permissions
RUN chmod +x execute.sh 

# Expose the ports used by the application
EXPOSE 8070 8071 5000

# Define the container startup command
ENTRYPOINT ["/bin/bash", "-c", "./execute.sh"]
