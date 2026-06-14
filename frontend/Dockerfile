FROM node:22-alpine

WORKDIR /app

# Install dependencies
COPY package*.json ./
RUN npm ci

# Copy source
COPY . .

# Expose Vite dev server port
EXPOSE 3000

# Run Vite dev server
CMD ["npx", "vite", "--host", "0.0.0.0", "--port", "3000"]
