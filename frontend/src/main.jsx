import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import App from "./App.jsx";
import Landing from "./pages/Landing.jsx";
import Chat from "./pages/Chat.jsx";
import History from "./pages/History.jsx";
import { ProfileProvider } from "./ProfileContext.jsx";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <ProfileProvider>
      <BrowserRouter>
        <Routes>
          <Route element={<App />}>
            <Route path="/" element={<Landing />} />
            <Route path="/chat" element={<Chat />} />
            <Route path="/historial" element={<History />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </ProfileProvider>
  </React.StrictMode>
);
