import webview
import os
from backend import Backend

def main():
    # Get path to static files
    static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')
    index_path = os.path.join(static_dir, 'index.html')
    
    if not os.path.exists(index_path):
        print(f"Error: {index_path} not found!")
        return

    # Create API instance
    api = Backend()

    # Create window with API
    window = webview.create_window(
        'CGRU Batch Submitter', 
        url=index_path,
        width=1400,
        height=900,
        resizable=True,
        background_color='#0f172a',
        js_api=api
    )
    
    webview.start(debug=False)

if __name__ == '__main__':
    main()