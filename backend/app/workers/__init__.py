"""ARQ workers. Currently a scaffold — the in-process runner in
``app.modules.images.service.run_job`` is the production path until Redis +
ARQ is provisioned. The signatures below let ``arq app.workers.main.WorkerSettings``
pick up our jobs once it is."""
