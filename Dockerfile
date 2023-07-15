FROM python:3.9.17-bullseye

RUN mkdir /app
WORKDIR /app

# Copy pyproject.toml and install dependencies
COPY pyproject.toml /app/
RUN pip install poetry
RUN poetry config virtualenvs.create false
RUN poetry install

# Copy the rest of the application (except poetry.lock)
COPY . /app

CMD [ "python", "main.py"]
