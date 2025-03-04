# Project: Scientific Articles Analysis with Grobid

## Description
This project analyzes 10 open-access articles related to Bitcoin using Grobid. It extracts key information and visualizes it in different formats (CSV, PNG). 

The main objectives of this project are:
- **Extract keywords and generate a word cloud** from abstracts.
- **Visualize the number of figures** in each article.
- **List all the links found in each paper.**


## Requirements
To run the project, install the following dependencies:

### Option 1: Running with Docker Compose (Recommended)
This method does **not** require installing Python or Conda.
- Install Docker from [Docker official website](https://www.docker.com/)

### Option 2: Running with Python and Conda (Manual Setup)
- Install Docker from [Docker official website](https://www.docker.com/)
- Import Grobid into Docker using the command:
```bash
  docker pull grobid/grobid:0.8.1
```
- Install Anaconda from [Anaconda official website](https://www.anaconda.com/download)
- Create a new environment using the `environment.yml` file:
```bash
  cd (project-directory)
  conda env create -f environment.yml
```
- Install Python from [Python official website](https://www.python.org/downloads/)

## Installation Instructions
1. Clone the repository:
```bash
   git clone https://github.com/javizhangg/task1-AIAOSIRSE.git
   cd task1-AIAOSIRSE
```

## Execution Instructions
There are two ways to execute the program:

### 1. Using Docker Compose (Recommended)
This method sets up the full environment without requiring Python or Conda.
1. Ensure Docker .
2. Open Docker desktop
3. Navigate to the project directory and run:
```bash
docker-compose up --build
```
This will automatically start all required services, including Grobid, and execute the pipeline.
4. When you have the image created you can execute the image with the comand
```bash
docker-compose up -d
```

### 2. Using the Manual Python Setup
1. Start Grobid using Docker:
```bash
   docker run --rm --init -p 8070:8070 -p 8071:8071 grobid/grobid:0.8.1
```

2. Activate the Conda environment:
```bash
   conda init 
   conda activate mi_entorno
```

3. Run the main script to process the articles:
```bash
python main.py
```

## Installation Instructions
1. Clone the repository:
```bash
   git clone https://github.com/javizhangg/task1-AIAOSIRSE.git
   cd task1-AIAOSIRSE
   ```

2. Start Grobid using Docker:
```bash
   docker run --rm --init -p 8070:8070 -p 8071:8071 grobid/grobid:0.8.1
   ```

3. Activate the Conda environment:
```bash
   conda init 
   conda activate <environment_name>
   ```

## Execution Instructions
### 1. Using the Current Method (Python Script)
Run the main script to process the articles:
```bash
python main.py
```

### 2. Using Docker Compose
Alternatively, you can use Docker Compose to run the project in a containerized environment. To do so:

1. Ensure Docker and Docker Compose are installed.
2. Navigate to the project directory and run:
```bash
docker-compose up --build
```
This will automatically start all required services, including Grobid, and execute the pipeline.


## Automated Testing and CI/CD
This project uses **GitHub Actions** for continuous integration. To manually trigger tests:
```bash
git push origin main
```
CI/CD workflows validate the installation and execution of tests.

## 📄 **Documentation**
Complete documentation is available on **ReadTheDocs**:
[![ReadTheDocs](https://readthedocs.org/projects/task1-aiaosirse/badge/?version=latest)](https://task1-aiaosirse.readthedocs.io/en/latest/)

It includes:
- **Introduction**
- **Execution Methods**
- **Citation**
---


## Preferred Citation
If you use this work, please cite it as:
```bibtex
@misc{ScientificArticlesAnalysis,
    title = {Scientific Articles Analysis with Grobid},
    howpublished = {\url{https://github.com/javizhangg/task1-AIAOSIRSE}},
    autor = {Zhiwei Zhang},
    publisher = {GitHub},
    year = {2025},
}
```
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.14905817.svg)](https://doi.org/10.5281/zenodo.14905817)

## License
This project is licensed under the [Apache License 2.0](LICENSE).

## Where to Get Help
For questions or issues, please use the forum or contact:
- **Author:** Zhiwei Zhang
- **Email:** Zhiwei.zha@alumnos.upm.es
- **GitHub:** [https://github.com/javizhangg](https://github.com/javizhangg)

## Acknowledgments
This project follows the best practices taught in the Open Science and AI course by Daniel Garijo, including reproducibility, metadata structuring, and documentation standards【43 source】【44 source】.

## 📄 Structured Metadata
This project includes metadata in [CodeMeta](https://codemeta.github.io/) format for easier discovery and reuse.

📌 The `codemeta.json` file can be found in the repository root:
🔗 [codemeta.json](https://github.com/javizhangg/task1-AIAOSIRSE/blob/main/codemeta.json)


