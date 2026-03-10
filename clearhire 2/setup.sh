#!/bin/bash
set -e
CYAN='\033[0;36m'; GREEN='\033[0;32m'; BOLD='\033[1m'; YELLOW='\033[1;33m'; NC='\033[0m'

echo ""
echo -e "${CYAN}${BOLD}==============================${NC}"
echo -e "${CYAN}${BOLD}   ClearHire Setup            ${NC}"
echo -e "${CYAN}${BOLD}==============================${NC}"
echo ""

if ! command -v python3 &>/dev/null; then
  echo "ERROR: Python not found. Install from https://python.org/downloads"; exit 1
fi
echo -e "${GREEN}✓ Python found${NC}"

python3 -m venv venv && source venv/bin/activate
echo -e "${GREEN}✓ Virtual environment created${NC}"

echo "Installing packages (takes ~30 seconds)..."
pip install -q -r requirements.txt
echo -e "${GREEN}✓ Packages installed${NC}"

echo ""
echo -e "${BOLD}You need two API keys:${NC}"
echo ""
echo "  Stripe    → https://dashboard.stripe.com → Developers → API Keys"
echo "  Anthropic → https://console.anthropic.com → API Keys"
echo ""
read -p "  Stripe PUBLISHABLE key (pk_test_...): " STRIPE_PUB
read -p "  Stripe SECRET key (sk_test_...):      " STRIPE_SEC
read -p "  Anthropic API key (sk-ant-...):        " ANTHROPIC_KEY
read -p "  Your domain (press ENTER for localhost): " DOMAIN
DOMAIN=${DOMAIN:-"http://localhost:5000"}
SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")

cat > .env << EOF
STRIPE_PUBLISHABLE_KEY=${STRIPE_PUB}
STRIPE_SECRET_KEY=${STRIPE_SEC}
ANTHROPIC_API_KEY=${ANTHROPIC_KEY}
YOUR_DOMAIN=${DOMAIN}
SECRET_KEY=${SECRET}
PORT=5000
EOF

echo ""
echo -e "${GREEN}${BOLD}================================${NC}"
echo -e "${GREEN}${BOLD}   Setup complete! ✅           ${NC}"
echo -e "${GREEN}${BOLD}================================${NC}"
echo ""
echo -e "  Start: ${CYAN}source venv/bin/activate && python app.py${NC}"
echo -e "  Open:  ${CYAN}http://localhost:5000${NC}"
echo ""
echo -e "${YELLOW}  Test card: 4242 4242 4242 4242 · expiry 12/34 · CVC 123${NC}"
echo ""
echo -e "${YELLOW}  Plans:${NC}"
echo -e "${YELLOW}    Basic \$9  → 7 days unlimited audits${NC}"
echo -e "${YELLOW}    Pro   \$29 → lifetime unlimited audits + rewrites${NC}"
echo ""
