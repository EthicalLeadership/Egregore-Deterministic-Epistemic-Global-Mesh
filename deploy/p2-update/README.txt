Pioneer 2 deployment package.

Copy this directory to Pioneer 2 (~/egregore/deploy/p2-update/), then run:

  cd ~/egregore
  ./deploy/p2-update/apply.sh

This will update the changed files, append federation env vars to .env,
install the systemd user units, and restart the services.
