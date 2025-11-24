import React, { useState, useEffect } from 'react';
import { 
  Container, 
  Typography, 
  Box, 
  Paper, 
  Grid,
  CircularProgress,
  Alert,
  Tab,
  Tabs,
  Button,
  Divider,
  LinearProgress,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle
} from '@mui/material';
import { useParams, useLocation, useNavigate } from 'react-router-dom';
import ReactMarkdown from 'react-markdown';
import LiveTaskList from '../components/LiveTaskList';
import DownloadIcon from '@mui/icons-material/Download';
import ContentCopyIcon from '@mui/icons-material/ContentCopy';
import { getGenerationStatus, getGenerationResult, reloadTasks, stopTask } from '../utils/api';

const defaultGenerationDetails = {
  prompt: 'Loading prompt...',
  model: 'Loading model...',
  type: 'unknown',
  status: 'unknown'
};

const ResultsPage = () => {
  const { id } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const [activeTab, setActiveTab] = useState(0);
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [generationStatus, setGenerationStatus] = useState('generating');
  const [copySuccess, setCopySuccess] = useState('');
  const [progress, setProgress] = useState(0);
  const [elapsedTime, setElapsedTime] = useState(0);
  const [stopConfirmOpen, setStopConfirmOpen] = useState(false);
  const [stopInProgress, setStopInProgress] = useState(false);

  // Track generation details from navigation state + async updates
  const [generationDetails, setGenerationDetails] = useState(
    () => location.state || defaultGenerationDetails
  );

  useEffect(() => {
    setGenerationDetails(location.state || defaultGenerationDetails);
  }, [location.state]);

  // Poll status and fetch result when ready
  useEffect(() => {
    let pollInterval;
    let pollCount = 0;
    
    const fetchStatus = async () => {
      try {
        setLoading(true);
        
        // Try to reload tasks first to ensure the task is in the backend's memory
        if (pollCount === 0) {
          try {
            await reloadTasks();
          } catch (reloadErr) {
            console.warn("Failed to reload tasks:", reloadErr);
          }
        }
        
        // Get status from backend
        const statusData = await getGenerationStatus(id);
        
        if (statusData) {
          setGenerationStatus(statusData.status);
          
          if (statusData.elapsedTime) {
            setElapsedTime(Math.round(statusData.elapsedTime));
          }
          
          // Update model information if available
          if (statusData.model && statusData.model !== 'unknown') {
            setGenerationDetails(prev => ({ ...prev, model: statusData.model }));
          }
          
          if (statusData.searchEngine) {
            setGenerationDetails(prev => ({ ...prev, searchEngine: statusData.searchEngine }));
          }
          
          if (statusData.prompt) {
            setGenerationDetails(prev => ({ ...prev, prompt: statusData.prompt }));
          }
          
          // Update progress based on status
          if (statusData.status === 'completed') {
            setProgress(100);
            
            // Fetch the result
            const resultData = await getGenerationResult(id);
            if (resultData && resultData.result) {
              setResult(resultData.result);
              
              // Update model information from result if available
              if (resultData.model && resultData.model !== 'unknown') {
                setGenerationDetails(prev => ({ ...prev, model: resultData.model }));
              }
              
              if (resultData.searchEngine) {
                setGenerationDetails(prev => ({ ...prev, searchEngine: resultData.searchEngine }));
              }
              
              if (resultData.prompt) {
                setGenerationDetails(prev => ({ ...prev, prompt: resultData.prompt }));
              }
              
              clearInterval(pollInterval);
              setLoading(false);
            }
          } else if (statusData.status === 'error') {
            setError(statusData.error || 'An error occurred during generation');
            clearInterval(pollInterval);
            setLoading(false);
          } else if (statusData.status === 'stopped') {
            setError('Task has been stopped by user request.');
            clearInterval(pollInterval);
            setLoading(false);
          } else {
            // Still processing, increment progress
            pollCount++;
            // Simple simulated progress: 10% immediately, then gradually up to 90% while waiting
            const simulatedProgress = Math.min(10 + 80 * (pollCount / 60), 90);
            setProgress(simulatedProgress);
          }
        }
      } catch (err) {
        console.error('Error polling status:', err);
        
        // If this is the first attempt and we got an error, try to load directly
        if (pollCount === 0) {
          try {
            // Try to get the result directly
            const resultData = await getGenerationResult(id);
            if (resultData && resultData.result) {
              setResult(resultData.result);
              setGenerationStatus('completed');
              setProgress(100);
              
              // Update model information from result if available
              if (resultData.model && resultData.model !== 'unknown') {
                setGenerationDetails(prev => ({ ...prev, model: resultData.model }));
              }
              
              if (resultData.searchEngine) {
                setGenerationDetails(prev => ({ ...prev, searchEngine: resultData.searchEngine }));
              }
              
              if (resultData.prompt) {
                setGenerationDetails(prev => ({ ...prev, prompt: resultData.prompt }));
              }
              
              clearInterval(pollInterval);
              setLoading(false);
              return;
            }
          } catch (directErr) {
            console.error('Error fetching result directly:', directErr);
          }
        }
        
        setError('Error checking generation status: ' + (err.message || 'Unknown error'));
        clearInterval(pollInterval);
        setLoading(false);
      }
    };
    
    // Initial fetch
    fetchStatus();
    
    // Set up polling every 5 seconds
    pollInterval = setInterval(fetchStatus, 5001);
    
    return () => {
      clearInterval(pollInterval);
    };
  }, [id]);

  const handleTabChange = (event, newValue) => {
    setActiveTab(newValue);
  };

  const handleTaskClick = (task) => {
    console.log('Task clicked:', task);
    // Here you could show details about the specific task
  };

  const handleDownload = () => {
    const element = document.createElement('a');
    const file = new Blob([result], {type: 'text/markdown'});
    element.href = URL.createObjectURL(file);
    element.download = `${id}.md`;
    document.body.appendChild(element);
    element.click();
    document.body.removeChild(element);
  };

  const handleCopyToClipboard = () => {
    navigator.clipboard.writeText(result).then(
      () => {
        setCopySuccess('Copied to clipboard!');
        setTimeout(() => setCopySuccess(''), 3000);
      },
      () => {
        setCopySuccess('Failed to copy');
        setTimeout(() => setCopySuccess(''), 3000);
      }
    );
  };
  
  const handleStopGeneration = () => {
    setStopInProgress(true);
    stopTask(id)
      .then(response => {
        setGenerationStatus('stopped');
        setError('Task has been stopped by user request.');
      })
      .catch(err => {
        setError(`Failed to stop task: ${err.message}`);
      })
      .finally(() => {
        setStopConfirmOpen(false);
        setStopInProgress(false);
      });
  };

  if (loading && generationStatus !== 'completed') {
    return (
      <Container maxWidth="lg" sx={{ mt: 8 }}>
        {/* Stop Confirmation Dialog */}
        <Dialog
          open={stopConfirmOpen}
          onClose={() => setStopConfirmOpen(false)}
          aria-labelledby="stop-dialog-title"
          aria-describedby="stop-dialog-description"
        >
          <DialogTitle id="stop-dialog-title">
            Confirm Stop Generation
          </DialogTitle>
          <DialogContent>
            <DialogContentText id="stop-dialog-description">
              Are you sure you want to stop this generation? This action cannot be undone, and the generation will be terminated immediately.
            </DialogContentText>
          </DialogContent>
          <DialogActions>
            <Button onClick={() => setStopConfirmOpen(false)} disabled={stopInProgress}>
              Cancel
            </Button>
            <Button 
              onClick={handleStopGeneration} 
              color="error" 
              autoFocus
              disabled={stopInProgress}
              startIcon={stopInProgress ? <CircularProgress size={16} /> : null}
            >
              {stopInProgress ? "Stopping..." : "Stop Generation"}
            </Button>
          </DialogActions>
        </Dialog>
        
        <Box sx={{ mb: 4 }}>
          <Typography variant="h5" sx={{ mb: 2, textAlign: 'center' }}>
            {generationStatus === 'generating' ? 'Generating content...' : 'Loading results...'}
          </Typography>
          
          <Paper elevation={3} sx={{ p: 3, mb: 4 }}>
            <Typography variant="h6" gutterBottom>
              Generation Details
            </Typography>
            <Divider sx={{ mb: 2 }} />
            
            <Typography
              variant="body1"
              sx={{ mb: 2, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}
            >
              <strong>Prompt:</strong> {generationDetails.prompt || ''}
            </Typography>
            
            <Typography variant="body1" sx={{ mb: 2 }}>
              <strong>Model:</strong> {generationDetails.model}
            </Typography>
            
            {generationDetails.searchEngine && (
              <Typography variant="body1" sx={{ mb: 2 }}>
                <strong>Search Engine:</strong> {generationDetails.searchEngine}
              </Typography>
            )}
            
            <Box sx={{ mb: 3 }}>
              <LinearProgress 
                variant="determinate" 
                value={progress} 
                sx={{ 
                  height: 8, 
                  borderRadius: 4,
                  mb: 1
                }} 
              />
              
              <Typography variant="body2" color="text.secondary" align="right">
                {progress.toFixed(2)}% complete
                {elapsedTime > 0
                  ? `· ${Math.floor(elapsedTime / 60)}:${String(elapsedTime % 60).padStart(2, '0')} elapsed`
                  : ''}
              </Typography>
            </Box>
            
            <Box sx={{ display: 'flex', justifyContent: 'center', mb: 2 }}>
              <Button 
                variant="contained" 
                color="error"
                onClick={() => setStopConfirmOpen(true)}
              >
                Stop Generation
              </Button>
            </Box>
            
            <Typography variant="body2" color="text.secondary">
              This may take several minutes depending on the complexity of the task.
            </Typography>
          </Paper>
          
          {/* Show live task list during generation */}
          <LiveTaskList taskId={id} onTaskClick={handleTaskClick} />
        </Box>
      </Container>
    );
  }

  if (error) {
    return (
      <Container maxWidth="lg" sx={{ mt: 8 }}>
        <Alert severity="error">
          {error}
        </Alert>
        <Box sx={{ mt: 2, textAlign: 'center' }}>
          <Button variant="contained" onClick={() => navigate(-1)}>
            Go Back
          </Button>
        </Box>
      </Container>
    );
  }

  return (
    <Container maxWidth="lg">
      <Box sx={{ mt: 4, mb: 6 }}>
        <Typography variant="h4" component="h1" gutterBottom>
          {generationDetails.type === 'story' ? 'Generated Story' : 'Generated Report'}
        </Typography>
        
        <Paper elevation={3} sx={{ p: 3, mb: 4 }}>
          <Typography variant="h6" gutterBottom>
            Generation Details
          </Typography>
          <Divider sx={{ mb: 2 }} />
          <Grid container spacing={2}>
            <Grid item xs={12} md={8}>
              <Typography variant="body1">
                <strong>Prompt:</strong> {generationDetails.prompt}
              </Typography>
            </Grid>
            <Grid item xs={12} md={4}>
              <Typography variant="body1">
                <strong>Model:</strong> {generationDetails.model}
              </Typography>
            </Grid>
            {generationDetails.searchEngine && (
              <Grid item xs={12} md={4}>
                <Typography variant="body1">
                  <strong>Search Engine:</strong> {generationDetails.searchEngine}
                </Typography>
              </Grid>
            )}
            <Grid item xs={12}>
              <Typography variant="body1">
                <strong>Status:</strong> {
                  generationStatus === 'completed' ? 
                  <span style={{ color: 'green' }}>Complete</span> : 
                  generationStatus === 'stopped' ?
                  <span style={{ color: 'red' }}>Stopped</span> :
                  <span style={{ color: 'orange' }}>In Progress</span>
                }
              </Typography>
            </Grid>
          </Grid>
        </Paper>
      </Box>

      <Box sx={{ mb: 4 }}>
        <Box sx={{ borderBottom: 1, borderColor: 'divider', mb: 2 }}>
          <Tabs value={activeTab} onChange={handleTabChange} aria-label="result tabs">
            <Tab label={generationStatus === 'completed' ? "Result" : "Generating..."} />
            <Tab 
              label={
                generationStatus === 'completed' ? 
                "Task Decomposition" : 
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                  Live Tasks
                  {generationStatus !== 'completed' && 
                    <Chip size="small" color="primary" label="Active" sx={{ height: 20 }} />
                  }
                </Box>
              } 
            />
          </Tabs>
        </Box>

        {activeTab === 0 && (
          <Paper elevation={3} sx={{ p: 3 }}>
            <Box sx={{ display: 'flex', justifyContent: 'flex-end', mb: 2, gap: 1 }}>
              <Button 
                variant="outlined" 
                startIcon={<ContentCopyIcon />}
                onClick={handleCopyToClipboard}
              >
                Copy to Clipboard
              </Button>
              <Button 
                variant="outlined" 
                startIcon={<DownloadIcon />}
                onClick={handleDownload}
              >
                Download
              </Button>
            </Box>
            {copySuccess && (
              <Alert severity="success" sx={{ mb: 2 }}>
                {copySuccess}
              </Alert>
            )}
            <Box className="markdown-content">
              <ReactMarkdown>
                {result}
              </ReactMarkdown>
            </Box>
          </Paper>
        )}

        {activeTab === 1 && (
          // Always use LiveTaskList for consistent display
          <LiveTaskList taskId={id} onTaskClick={handleTaskClick} />
        )}
      </Box>
    </Container>
  );
};

export default ResultsPage;