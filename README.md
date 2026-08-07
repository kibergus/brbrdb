# brbrdb

Karting race database and telemetry analysis web app.

---

## Motivation

This project started as a way to better understand how my daughter progresses on her karting journey, how races unfold, and how she compares to other drivers. It began with a couple of simple dashboards and gradually grew into a full database and telemetry platform.

Telemetry is an invaluable source of insights for driver development, helping focus on specific areas for improvement. Access to powerful data analysis shouldn't cost a fortune. **brbrdb** is shared in the hope that it will help other drivers grow, based on the belief that making structured racing insights accessible to everyone is the right thing to do.

---

## Key Features

- **Race Database & Driver Profiles**: Tracks session results, sector times, penalties, and driver lap histories across leagues and kart classes.
- **Interactive Telemetry & GPS Visualization**: Graph lap comparisons, velocity profiles, telemetry trajectories, and sector speed deltas on interactive track maps.

---

## Running Tests

Run both Python (`pytest`) and JavaScript (`vitest`) test suites:

```bash
./test.sh
```

To run test suites individually:
- Python tests: `pytest`
- JavaScript tests: `npm test`

---

## Production Deployment

Production deployments run via Docker and Gunicorn:

```bash
docker-compose up -d --build
```

---

## License

Licensed under the [Apache License, Version 2.0](LICENSE).
