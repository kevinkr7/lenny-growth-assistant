FROM node:22.19-alpine
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts --omit=dev
COPY src ./src
ENV NODE_ENV=production PI_AGENT_HOST=0.0.0.0 PI_AGENT_PORT=8001
USER node
EXPOSE 8001
CMD ["node", "src/server.js"]
