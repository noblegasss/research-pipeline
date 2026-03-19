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
      script: "npx",
      args: "next dev -p 3010",
      interpreter: "none",
      autorestart: true,
      max_restarts: 10,
    },
  ],
};
