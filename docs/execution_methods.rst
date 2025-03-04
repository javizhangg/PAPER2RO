Execution Methods
=================

This project provides two ways to set up and run the environment:

1. **Using Docker Compose (Recommended) - Fully automated setup**
2. **Manual Setup (Python + Conda) - Custom setup**

Each method has its advantages depending on your requirements.

.. contents:: Table of Contents
   :depth: 2
   :local:

----------------------------------------

Using Docker Compose (Recommended)
----------------------------------------

Why choose this?
================
✅ No need to install Python or Conda manually  
✅ Automatically sets up Grobid and all dependencies  
✅ Ensures a fully containerized and reproducible environment  

### **Steps to use Docker Compose**
1. **Install Docker** (if not installed) from `Docker official website <https://www.docker.com/>`_  
2. **Open Docker Desktop** and ensure it is running  
3. **Navigate to the project folder** and run:  

   .. code-block:: bash

      docker-compose up --build

   This command:
   - Builds the Docker image
   - Sets up the environment automatically
   - Runs Grobid and executes the pipeline  

4. **Run the pre-built image** (after the first build):  

   .. code-block:: bash

      docker-compose up -d

   This keeps the container running in the background.

5. **Stop and clean up** when finished:  

   .. code-block:: bash

      docker-compose down

----------------------------------------

Using Python and Conda (Manual Setup)
----------------------------------------

Why choose this?
================
✅ Useful for debugging and custom configurations  
✅ Allows you to modify dependencies easily  
✅ No need to build Docker images  

### **Steps for Manual Setup**
1. **Install Docker and pull the Grobid image**  

   .. code-block:: bash

      docker pull grobid/grobid:0.8.1

2. **Start Grobid manually**  

   .. code-block:: bash

      docker run --rm --init -p 8070:8070 -p 8071:8071 grobid/grobid:0.8.1

3. **Install Anaconda and create the environment**  

   .. code-block:: bash

      conda env create -f environment.yml
      conda activate mi_entorno

4. **Run the project manually**  

   .. code-block:: bash

      python main.py

----------------------------------------

Summary: Which Method Should You Use?
----------------------------------------

.. list-table:: **Comparison of Execution Methods**
   :header-rows: 1

   * - Method
     - Pros
     - Cons
   * - **Docker Compose** (Recommended)
     - ✅ Fully automated  
       ✅ No need to install Python/Conda  
       ✅ Ensures reproducibility
     - ❌ Requires Docker installed
   * - **Manual Setup** (Python + Conda)
     - ✅ More flexibility  
       ✅ Easier to debug dependencies
     - ❌ Requires manual installation  
       ❌ Takes longer to set up

### **Recommendation**
- If you **just want to run the project quickly**, use **Docker Compose**.
- If you **need more control or want to debug the code**, use the **manual setup**.

----------------------------------------

