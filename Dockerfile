FROM nexus-repo.*****/***/python-base:v3.10-2

COPY requirements.txt requirements.txt

RUN pip install -r requirements.txt

COPY owners-to-omd.py owners-to-omd.py
