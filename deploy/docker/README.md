# Docker delivery

The Docker delivery uses one image and two services: `web` and `worker`.

- Program code and fixed UI/PDF assets are stored in the image.
- `./runtime` is mounted read/write at `/app/runtime`. Place the formal database at
  `runtime/db/airport_group.sqlite3` before startup.
- `./resources/tiles` is mounted read-only into the Web service at
  `/app/resources/tiles`. The Worker does not read map tiles.

## Before first use

- Install and start Docker Desktop.
- Import the Docker image supplied separately on the delivery media.
- Place the offline tiles under `resources/tiles`.
- Ensure the formal database exists at `runtime/db/airport_group.sqlite3`.

## Daily use

- Double-click `AirportGroupLauncher.exe`; the default browser opens automatically.
- To stop the system, open the Launcher and click **关闭系统**.
- Closing the Launcher alone leaves the Docker services running.

The Launcher does not install Docker, load the Docker image, copy map tiles, or
initialize the formal database.
