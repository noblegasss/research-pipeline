const path = require("path");
const DIR = __dirname;

module.exports = {
  apps: [
    {
      name: "research-api",
      cwd: DIR,
      script: "python3",
      args: "-m uvicorn api.main:app --host 127.0.0.1 --port 8010",
      interpreter: "none",
      autorestart: true,
      max_restarts: 10,
    },
    {
      name: "research-web",
      cwd: path.join(DIR, "web"),
      script: "node_modules/.bin/next",
      args: "dev -p 3010",
      autorestart: true,
      max_restarts: 10,
    },
  ],
};
