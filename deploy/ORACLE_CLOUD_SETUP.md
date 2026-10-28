# 🚀 Oracle Cloud Always-Free Deployment Guide for JobHunt

Oracle Cloud Infrastructure (OCI) offers an **Always Free Tier** that gives you a full dedicated Linux server running 24/7/365 for $0. It never goes to sleep, has no cold starts, and gives you a static public IP address.

---

## 📋 What You Get for Free on Oracle Cloud

* **Choice 1 (Best)**: **Ampere A1 (ARM64)** — Up to 4 cores, 24 GB RAM, 100% free forever.
* **Choice 2**: **VM.Standard.E2.1.Micro (AMD)** — 1 core, 1 GB RAM, 100% free forever.
* **Network**: Static Public IPv4 address included.
* **Storage**: 200 GB NVMe block storage included.

---

## Step 1: Create Your Always-Free Instance in OCI Console

1. Log in to [cloud.oracle.com](https://cloud.oracle.com/).
2. In the top-left menu, navigate to **Compute** → **Instances** → click **Create instance**.
3. Configure the VM:
   * **Name**: `jobhunt-server`
   * **Image and Shape**:
     * Click **Edit**.
     * **Image**: Select **Canonical Ubuntu** (Ubuntu 24.04 or 22.04 Minimal/Standard).
     * **Shape**: Select **Ampere** (VM.Standard.A1.Flex) and assign **2 OCPUs, 12 GB RAM** (labeled *Always Free Eligible*), OR select **Specialty and legacy** → **VM.Standard.E2.1.Micro** (Always Free Eligible).
4. **Networking**:
   * Keep default VCN and Subnet.
   * Ensure **Assign a public IPv4 address** is set to **Yes**.
5. **Add SSH keys**:
   * Select **Generate a key pair for me**.
   * Click **Save private key** (downloads a `.key` file to your computer — **do not lose this!**).
6. Click **Create**. The instance will be ready in ~60 seconds with a **Public IP Address** (e.g. `129.153.xxx.xxx`).

---

## Step 2: Open Ingress Ports in Oracle Cloud Console (CRITICAL)

By default, Oracle Cloud blocks all ports except Port 22 (SSH). You must allow port `8000` (and `80`/`443` if using HTTPS):

1. In your Instance details page, click on your **Virtual Cloud Network (VCN)** name.
2. Under **Security Lists** on the left menu, click **Default Security List for...**.
3. Click **Add Ingress Rules**:
   * **Source Type**: `CIDR`
   * **Source CIDR**: `0.0.0.0/0`
   * **IP Protocol**: `TCP`
   * **Destination Port Range**: `8000, 80, 443`
   * **Description**: `JobHunt Bridge and Webhook Ports`
4. Click **Add Ingress Rules**.

---

## Step 3: Connect to Your Oracle Server via SSH

From your local terminal, change permissions on your downloaded SSH key and connect:

```bash
# Set key permissions
chmod 400 ~/Downloads/ssh-key-*.key

# Connect to the instance (Ubuntu username is 'ubuntu')
ssh -i ~/Downloads/ssh-key-*.key ubuntu@<YOUR_ORACLE_PUBLIC_IP>
```

---

## Step 4: Clone JobHunt & Run Automated Setup

Once inside your Oracle VM terminal:

```bash
# 1. Clone your JobHunt repository
git clone https://github.com/<your-username>/JobHunt.git
cd JobHunt

# 2. Create and configure your .env file
nano .env
```

Paste your environment variables into `.env`:
```env
# Gmail SMTP
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=kartikdewnani56@gmail.com
SMTP_PASS=your_gmail_app_password
MAIL_TO=kartikdewnani56@gmail.com

# X (Twitter) Session Cookies
X_AUTH_TOKEN=your_x_auth_token_here
X_CT0=your_x_ct0_here
```
Press `Ctrl+O` then `Enter` to save, and `Ctrl+X` to exit.

```bash
# 3. Run the automated setup script
./deploy/setup_oracle.sh
```

### What this script does automatically:
* Fixes the internal Oracle Ubuntu `iptables` rules that reject non-SSH traffic.
* Creates the Python virtual environment and installs all dependencies (`fastapi`, `uvicorn`, `httpx[http2]`, etc.).
* Sets up a **systemd service** (`jobhunt.service`) that runs the server 24/7 in the background and auto-restarts on server reboots.

---

## Step 5: Verify the Service is Running

Test from your local computer or phone browser:
```bash
# Test health endpoint
curl http://<YOUR_ORACLE_PUBLIC_IP>:8000/health
```

Expected response:
```json
{
  "status": "online",
  "service": "JobHunt Bridge for Dify.ai",
  "has_profile": true,
  "configured_locations": ["bangalore", "bengaluru", "pune", "hyderabad", "india"]
}
```

---

## Step 6: Connect Dify Cloud to Your Oracle Server

In your Dify Studio workflow:

1. Open **Environment Variables** in Dify.
2. Update **`BRIDGE_API_URL`**:
   * Set it to: `http://<YOUR_ORACLE_PUBLIC_IP>:8000`
   *(Or if you prefer HTTPS, see Step 7 below for a 1-minute free Cloudflare Tunnel).*
3. Click **Publish** → **Update**.

Now Dify can reach your dedicated Oracle server at any time with zero cold starts!

---

## Step 7 (Optional): Free HTTPS with Cloudflare Tunnel

If you want a free, secure `https://` domain without buying a domain name or handling SSL certificates:

Run this on your Oracle VM:
```bash
# Download cloudflared
curl -L --output cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
sudo dpkg -i cloudflared.deb

# Start tunnel as a background service
cloudflared tunnel --url http://127.0.0.1:8000
```
Cloudflare will give you an instant `https://<unique-name>.trycloudflare.com` URL that points securely to your Oracle server.

---

## Step 8: Useful Service Commands

```bash
# View live logs
sudo journalctl -u jobhunt.service -f

# Check server status
sudo systemctl status jobhunt.service

# Restart server after code changes
sudo systemctl restart jobhunt.service

# Stop server
sudo systemctl stop jobhunt.service
```
